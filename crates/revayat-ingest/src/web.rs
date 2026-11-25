use std::{
    collections::BTreeMap,
    io::Read,
    net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, ToSocketAddrs},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

use encoding_rs::{Encoding, UTF_8, WINDOWS_1252};
use regex::bytes::Regex;
use reqwest::{
    blocking::Client,
    header::{ACCEPT, CONTENT_TYPE, LOCATION},
    redirect::Policy,
};
use revayat_core::{
    AssetRecord, BookDocument, Chapter, ChapterProvenance, SourceFormat, sha256_bytes,
};
use scraper::Html;
use serde::{Deserialize, Serialize};
use url::{Host, Url};

use crate::{
    IngestError,
    assets::{
        MAX_IMAGE_BYTES, MAX_TEXT_BYTES, canonical_local, image_block, read_file, source_title,
        store_image, uri_part,
    },
    dom,
    error::{invalid, io},
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FetchedPage {
    pub url: String,
    pub html: String,
    pub content_type: Option<String>,
}

fn public_v4(addr: Ipv4Addr) -> bool {
    let [a, b, c, _] = addr.octets();
    !(matches!(a, 0 | 10 | 127 | 224..=255)
        || a == 100 && (64..=127).contains(&b)
        || a == 169 && b == 254
        || a == 172 && (16..=31).contains(&b)
        || a == 192 && (b == 168 || b == 0 && matches!(c, 0 | 2) || b == 88 && c == 99)
        || a == 198 && (matches!(b, 18 | 19) || b == 51 && c == 100)
        || a == 203 && b == 0 && c == 113)
}

fn public_v6(addr: Ipv6Addr) -> bool {
    if let Some(mapped) = addr.to_ipv4_mapped() {
        return public_v4(mapped);
    }
    let [a, b, ..] = addr.segments();
    (0x2000..=0x3fff).contains(&a)
        && a != 0x2002
        && a != 0x3fff
        && !(a == 0x2001 && (b <= 0x1ff || b == 0x0db8))
}

fn public(addr: IpAddr) -> bool {
    match addr {
        IpAddr::V4(v) => public_v4(v),
        IpAddr::V6(v) => public_v6(v),
    }
}

fn validate_url(url: &Url) -> Result<(), IngestError> {
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err(IngestError::UnsafeUrl(
            "expected credential-free HTTP or HTTPS",
        ));
    }
    if url.port().is_some() {
        return Err(IngestError::UnsafeUrl("use the scheme's standard port"));
    }
    let valid = match url.host() {
        Some(Host::Ipv4(ip)) => public_v4(ip),
        Some(Host::Ipv6(ip)) => public_v6(ip),
        Some(Host::Domain(d)) => {
            let d = d.trim_end_matches('.');
            d.contains('.')
                && !["localhost", "local", "internal", "home.arpa"]
                    .iter()
                    .any(|suffix| {
                        d == *suffix
                            || d.strip_suffix(suffix)
                                .is_some_and(|prefix| prefix.ends_with('.'))
                    })
        }
        None => false,
    };
    if !valid {
        return Err(IngestError::UnsafeUrl("destination is not public"));
    }
    Ok(())
}

fn parse_url(raw: &str) -> Result<Url, IngestError> {
    if raw.trim() != raw || raw.chars().any(char::is_control) || raw.contains('\\') {
        return Err(IngestError::UnsafeUrl("invalid characters"));
    }
    let mut url = Url::parse(raw).map_err(|_| IngestError::UnsafeUrl("invalid URL"))?;
    validate_url(&url)?;
    url.set_fragment(None);
    Ok(url)
}

fn remaining(deadline: Instant) -> Result<Duration, IngestError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|d| !d.is_zero())
        .ok_or(IngestError::Timeout("source retrieval"))
}

fn dns_addresses(
    domain: &str,
    port: u16,
    deadline: Instant,
) -> Result<Vec<SocketAddr>, IngestError> {
    static WORKERS: AtomicUsize = AtomicUsize::new(0);
    let timeout = remaining(deadline)?;
    WORKERS
        .try_update(Ordering::AcqRel, Ordering::Acquire, |n| {
            (n < 8).then_some(n + 1)
        })
        .map_err(|_| IngestError::Limit("concurrent DNS lookups"))?;
    struct Active;
    impl Drop for Active {
        fn drop(&mut self) {
            WORKERS.fetch_sub(1, Ordering::AcqRel);
        }
    }
    let active = Active;
    let (tx, rx) = mpsc::sync_channel(1);
    // An absolute DNS name avoids resolver search suffixes for public input.
    let name = format!("{}.", domain.trim_end_matches('.'));
    thread::Builder::new()
        .name("ingest-dns".into())
        .spawn(move || {
            let _active = active;
            let addrs = (name.as_str(), port)
                .to_socket_addrs()
                .map(|r| r.collect::<Vec<_>>());
            let _ = tx.send(addrs);
        })
        .map_err(IngestError::Response)?;
    let addresses = rx
        .recv_timeout(timeout)
        .map_err(|e| match e {
            mpsc::RecvTimeoutError::Timeout => IngestError::Timeout("DNS lookup"),
            mpsc::RecvTimeoutError::Disconnected => invalid("DNS", "resolver stopped"),
        })?
        .map_err(IngestError::Response)?;
    if addresses.is_empty() || addresses.iter().any(|addr| !public(addr.ip())) {
        return Err(IngestError::UnsafeUrl(
            "DNS did not resolve only to public addresses",
        ));
    }
    Ok(addresses)
}

fn http_error(error: reqwest::Error) -> IngestError {
    if error.is_timeout() {
        IngestError::Timeout("source retrieval")
    } else {
        IngestError::Http(error.without_url())
    }
}

fn fetch(
    mut url: Url,
    deadline: Instant,
    limit: u64,
    image: bool,
) -> Result<(Url, Option<String>, Vec<u8>), IngestError> {
    let mut visited = Vec::new();
    for _ in 0..=5 {
        validate_url(&url)?;
        if visited.contains(&url) {
            return Err(invalid("HTTP redirect", "redirect loop"));
        }
        visited.push(url.clone());
        let mut builder = Client::builder()
            .no_proxy()
            .redirect(Policy::none())
            .retry(reqwest::retry::never());
        if let Some(Host::Domain(domain)) = url.host() {
            let addresses = dns_addresses(
                domain,
                url.port_or_known_default()
                    .ok_or(IngestError::UnsafeUrl("no standard port"))?,
                deadline,
            )?;
            builder = builder.resolve_to_addrs(domain, &addresses);
        }
        let timeout = remaining(deadline)?;
        let client = builder
            .timeout(timeout)
            .connect_timeout(timeout)
            .build()
            .map_err(http_error)?;
        let mut response = client
            .get(url.clone())
            .timeout(remaining(deadline)?)
            .header(
                ACCEPT,
                if image {
                    "image/*"
                } else {
                    "text/html, application/xhtml+xml"
                },
            )
            .send()
            .map_err(http_error)?;
        let status = response.status();
        if matches!(status.as_u16(), 301 | 302 | 303 | 307 | 308) {
            let location = response
                .headers()
                .get(LOCATION)
                .and_then(|h| h.to_str().ok())
                .ok_or_else(|| invalid("HTTP redirect", "missing or invalid Location header"))?;
            if location.contains('\\') || location.chars().any(char::is_control) {
                return Err(IngestError::UnsafeUrl("invalid redirect location"));
            }
            let mut next = url
                .join(location)
                .map_err(|_| IngestError::UnsafeUrl("invalid redirect location"))?;
            if url.scheme() == "https" && next.scheme() != "https" {
                return Err(IngestError::UnsafeUrl(
                    "HTTPS redirect downgrades transport",
                ));
            }
            next.set_fragment(None);
            url = next;
            continue;
        }
        if !status.is_success() {
            return Err(IngestError::HttpStatus(status));
        }
        let media = response
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        if let Some(kind) = media
            .as_deref()
            .and_then(|v| v.split(';').next())
            .map(|v| v.trim().to_ascii_lowercase())
            && if image {
                !kind.starts_with("image/")
            } else {
                !matches!(kind.as_str(), "text/html" | "application/xhtml+xml")
            }
        {
            return Err(invalid("HTTP response", "unexpected content type"));
        }
        if response.content_length().is_some_and(|n| n > limit) {
            return Err(IngestError::Limit("HTTP response size"));
        }
        let mut bytes = Vec::new();
        let mut buf = [0; 16 * 1024];
        loop {
            remaining(deadline)?;
            let n = response.read(&mut buf).map_err(|error| {
                if error.kind() == std::io::ErrorKind::TimedOut
                    || error
                        .get_ref()
                        .and_then(|e| e.downcast_ref::<reqwest::Error>())
                        .is_some_and(reqwest::Error::is_timeout)
                {
                    IngestError::Timeout("source retrieval")
                } else {
                    IngestError::Response(error)
                }
            })?;
            remaining(deadline)?;
            if n == 0 {
                break;
            }
            if bytes.len() as u64 + n as u64 > limit {
                return Err(IngestError::Limit("HTTP response size"));
            }
            bytes.extend_from_slice(&buf[..n]);
        }
        return Ok((url, media, bytes));
    }
    Err(IngestError::Limit("HTTP redirects"))
}

fn decode(bytes: &[u8], meta: Option<&str>) -> Result<String, IngestError> {
    let declared = meta
        .into_iter()
        .flat_map(|value| value.split(';').skip(1))
        .filter_map(|s| s.split_once('='))
        .find(|(k, _)| k.trim().eq_ignore_ascii_case("charset"))
        .and_then(|(_, v)| Encoding::for_label(v.trim().trim_matches(['\'', '"']).as_bytes()));
    let encoding = match (Encoding::for_bom(bytes), declared) {
        (Some((encoding, offset)), _) => return decode_as(encoding, &bytes[offset..]),
        (None, Some(encoding)) => encoding,
        (None, None) => {
            let pattern = Regex::new(r#"(?i)<meta\b[^>]*?\bcharset\s*=\s*['\"]?([a-z0-9_:-]+)"#)
                .map_err(|e| invalid("HTML encoding", e.to_string()))?;
            pattern
                .captures(&bytes[..bytes.len().min(4096)])
                .and_then(|c| c.get(1))
                .and_then(|m| Encoding::for_label(m.as_bytes()))
                .unwrap_or_else(|| {
                    if std::str::from_utf8(bytes).is_ok() {
                        UTF_8
                    } else {
                        WINDOWS_1252
                    }
                })
        }
    };
    decode_as(encoding, bytes)
}

fn decode_as(encoding: &'static Encoding, bytes: &[u8]) -> Result<String, IngestError> {
    let (text, malformed) = encoding.decode_without_bom_handling(bytes);
    if malformed {
        return Err(invalid(
            "HTML encoding",
            "response is not valid in the declared encoding",
        ));
    }
    Ok(text.into_owned())
}

pub fn fetch_url(url: &str, timeout_secs: u64) -> Result<FetchedPage, IngestError> {
    let url = parse_url(url)?;
    if timeout_secs == 0 {
        return Err(IngestError::Timeout("source retrieval"));
    }
    let (url, content_type, bytes) = fetch(
        url,
        Instant::now() + Duration::from_secs(timeout_secs.min(300)),
        MAX_TEXT_BYTES,
        false,
    )?;
    Ok(FetchedPage {
        url: url.into(),
        html: decode(&bytes, content_type.as_deref())?,
        content_type,
    })
}

fn chapter(
    html: &str,
    base: Option<&Url>,
    id: String,
    index: u32,
    image: impl FnMut(&str, &str, String) -> Result<Option<revayat_core::Block>, IngestError>,
) -> Result<Chapter, IngestError> {
    if html.len() as u64 > MAX_TEXT_BYTES {
        return Err(IngestError::Limit("HTML text size"));
    }
    let doc = Html::parse_document(html);
    let mut root = doc.root_element();
    for css in [
        "[itemprop=articleBody], #chapter-content, .chapter-content, .entry-content",
        "article",
        "main",
        "body",
    ] {
        if let Some(el) = doc
            .select(&dom::selector(css)?)
            .max_by_key(|&el| el.text().map(str::len).sum::<usize>())
        {
            root = el;
            break;
        }
    }
    let mut blocks = dom::blocks(root, &id, true, image)?;
    if blocks.is_empty() {
        return Err(invalid("HTML", "no chapter content"));
    }
    dom::visit_runs(&mut blocks, &mut |_, run| {
        run.footnote_ref = None;
        if let Some(link) = run.hyperlink.take() {
            if link.chars().any(char::is_control) || link.contains('\\') {
                return Ok(());
            }
            let resolved = Url::parse(&link)
                .ok()
                .or_else(|| base.and_then(|base| base.join(&link).ok()));
            run.hyperlink = match resolved {
                Some(url)
                    if matches!(url.scheme(), "http" | "https" | "mailto")
                        && url.username().is_empty()
                        && url.password().is_none() =>
                {
                    Some(url.into())
                }
                None if !link.contains(':') && !link.starts_with("//") => Some(link),
                _ => None,
            };
        }
        Ok(())
    })?;
    let title = dom::first_heading(&blocks)
        .or_else(|| {
            doc.select(&dom::selector("title").ok()?)
                .next()
                .map(dom::text)
        })
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| format!("Chapter {index}"));
    Ok(Chapter {
        id,
        index,
        title,
        blocks,
        provenance: Some(ChapterProvenance {
            source_sha256: sha256_bytes(html.as_bytes()),
            source_locator: base.map(|u| u.as_str().into()),
            pages: vec![],
        }),
    })
}

pub fn parse_html_chapter(
    html: &str,
    base_url: Option<&str>,
    assets_dir: Option<&Path>,
) -> Result<Chapter, IngestError> {
    let base = base_url.map(parse_url).transpose()?;
    remote_chapter(
        html,
        base.as_ref(),
        assets_dir,
        Instant::now() + Duration::from_secs(60),
        &mut BTreeMap::new(),
    )
}

fn remote_chapter(
    html: &str,
    base: Option<&Url>,
    assets_dir: Option<&Path>,
    deadline: Instant,
    assets: &mut BTreeMap<String, AssetRecord>,
) -> Result<Chapter, IngestError> {
    let mut downloaded = BTreeMap::new();
    let mut total = 0;
    chapter(html, base, "ch0001".into(), 1, |src, alt, id| {
        let (Some(base), Some(dir)) = (base, assets_dir) else {
            return Ok(None);
        };
        if src.starts_with("data:") {
            return Ok(None);
        }
        let url = base
            .join(src)
            .map_err(|_| IngestError::UnsafeUrl("invalid image source"))?;
        let key = url.as_str().to_owned();
        let asset = if let Some(prior) = downloaded.get(&key) {
            AssetRecord::clone(prior)
        } else {
            if downloaded.len() >= 64 || total >= MAX_IMAGE_BYTES {
                return Err(IngestError::Limit("HTML assets"));
            }
            let (_, _, bytes) = fetch(url, deadline, MAX_IMAGE_BYTES - total, true)?;
            total += bytes.len() as u64;
            let record = store_image(dir, assets, &bytes, true)?;
            downloaded.insert(key, record.clone());
            record
        };
        Ok(Some(image_block(id, asset, alt)))
    })
}

pub fn import_url(
    url: &str,
    assets_dir: &Path,
    timeout_secs: u64,
) -> Result<BookDocument, IngestError> {
    let url = parse_url(url)?;
    if timeout_secs == 0 {
        return Err(IngestError::Timeout("source retrieval"));
    }
    let deadline = Instant::now() + Duration::from_secs(timeout_secs.min(300));
    let (url, media, raw) = fetch(url, deadline, MAX_TEXT_BYTES, false)?;
    let html = decode(&raw, media.as_deref())?;
    let mut assets = BTreeMap::new();
    let mut chapter = remote_chapter(&html, Some(&url), Some(assets_dir), deadline, &mut assets)?;
    let hash = sha256_bytes(&raw);
    if let Some(provenance) = &mut chapter.provenance {
        provenance.source_sha256.clone_from(&hash);
    }
    let mut book = BookDocument::new(SourceFormat::Html, hash, &chapter.title, "und");
    book.chapters.push(chapter);
    book.assets = assets.into_values().collect();
    book.validate()?;
    Ok(book)
}

pub fn import_html_files(
    paths: &[PathBuf],
    assets_dir: &Path,
) -> Result<BookDocument, IngestError> {
    let first = paths
        .first()
        .ok_or_else(|| invalid("HTML", "no chapter files"))?;
    if paths.len() > 20_000 {
        return Err(IngestError::Limit("HTML chapter count"));
    }
    let mut book = BookDocument::new(
        SourceFormat::Html,
        String::new(),
        source_title(first),
        "und",
    );
    let mut assets = BTreeMap::new();
    let mut digests = Vec::new();
    for (index, path) in paths.iter().enumerate() {
        let raw = read_file(path, MAX_TEXT_BYTES)?;
        let digest = sha256_bytes(&raw);
        let html = decode(&raw, None)?;
        let root = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let mut current = chapter(
            &html,
            None,
            format!("ch{:04}", index + 1),
            (index + 1) as u32,
            |src, alt, id| {
                if Url::parse(src).is_ok() && !src.to_ascii_lowercase().starts_with("file:") {
                    return Ok(None);
                }
                if src.starts_with('/') || src.contains(['\\', ':']) {
                    return Err(IngestError::UnsafePath(src.into()));
                }
                let raw_path = src.split(['?', '#']).next().unwrap_or("");
                let parts: Vec<_> = raw_path
                    .split('/')
                    .map(uri_part)
                    .collect::<Result<_, _>>()?;
                if parts.iter().any(|p| p == ".." || p.is_empty()) {
                    return Err(IngestError::UnsafePath(src.into()));
                }
                let dir = root.canonicalize().map_err(|e| io("resolve", root, e))?;
                let image_path = canonical_local(&dir, &dir.join(parts.join("/")))?;
                let bytes = read_file(&image_path, MAX_IMAGE_BYTES)?;
                Ok(Some(image_block(
                    id,
                    store_image(assets_dir, &mut assets, &bytes, true)?,
                    alt,
                )))
            },
        )?;
        if let Some(provenance) = &mut current.provenance {
            provenance.source_sha256 = digest.clone();
            provenance.source_locator = path
                .file_name()
                .and_then(std::ffi::OsStr::to_str)
                .map(str::to_owned);
        }
        digests.push(digest);
        book.chapters.push(current);
    }
    book.source_sha256 = if let [only] = digests.as_slice() {
        only.clone()
    } else {
        sha256_bytes(digests.concat().as_bytes())
    };
    book.assets = assets.into_values().collect();
    book.validate()?;
    Ok(book)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denies_nonpublic_addresses_even_when_mapped() {
        for s in [
            "100.64.0.1",
            "192.0.2.1",
            "198.18.0.1",
            "224.0.0.1",
            "::ffff:127.0.0.1",
            "2002:7f00:1::",
            "fe80::1",
            "64:ff9b::a9fe:a9fe",
        ] {
            assert!(!s.parse::<IpAddr>().is_ok_and(public), "{s}");
        }
        for s in ["1.1.1.1", "2606:4700:4700::1111"] {
            assert!(s.parse::<IpAddr>().is_ok_and(public), "{s}");
        }
    }
}
