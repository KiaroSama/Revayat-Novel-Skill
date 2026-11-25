FROM rust:1.100.0-slim-bookworm AS builder
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake g++ make \
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY crates/ crates/
COPY skills/ skills/
COPY evaluation/cases.json evaluation/cases.json
RUN cargo build --release --locked --bin revayat-novel \
    && install -Dm755 target/release/revayat-novel bin/revayat-novel

FROM debian:bookworm-slim
WORKDIR /opt/revayat-novel
COPY --from=builder /build/bin/revayat-novel /usr/local/bin/revayat-novel
COPY --from=builder /build/skills/ skills/
COPY --from=builder /build/evaluation/cases.json evaluation/cases.json
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/revayat-novel"]
