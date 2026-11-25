use serde::{Deserialize, Serialize};

use crate::CoreError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Orientation {
    Portrait,
    Landscape,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PageGeometry {
    pub width_twips: u32,
    pub height_twips: u32,
    pub margin_top_twips: u32,
    pub margin_bottom_twips: u32,
    pub margin_inner_twips: u32,
    pub margin_outer_twips: u32,
    pub orientation: Orientation,
}

impl Default for PageGeometry {
    fn default() -> Self {
        Self {
            width_twips: 9354,
            height_twips: 13323,
            margin_top_twips: 1240,
            margin_bottom_twips: 1240,
            margin_inner_twips: 1240,
            margin_outer_twips: 1000,
            orientation: Orientation::Portrait,
        }
    }
}

impl PageGeometry {
    pub fn from_points(width: f64, height: f64, margins: [f64; 4]) -> Result<Self, CoreError> {
        let [top, bottom, inner, outer] = margins;
        let page = Self {
            width_twips: points_to_twips(width)?,
            height_twips: points_to_twips(height)?,
            margin_top_twips: points_to_twips(top)?,
            margin_bottom_twips: points_to_twips(bottom)?,
            margin_inner_twips: points_to_twips(inner)?,
            margin_outer_twips: points_to_twips(outer)?,
            orientation: if width > height {
                Orientation::Landscape
            } else {
                Orientation::Portrait
            },
        };
        page.validate()?;
        Ok(page)
    }

    pub fn validate(&self) -> Result<(), CoreError> {
        if self
            .margin_top_twips
            .saturating_add(self.margin_bottom_twips)
            >= self.height_twips
            || self
                .margin_inner_twips
                .saturating_add(self.margin_outer_twips)
                >= self.width_twips
        {
            return Err(CoreError::InvalidLayout(
                "page dimensions must leave room inside the margins",
            ));
        }
        Ok(())
    }
}

pub fn points_to_twips(points: f64) -> Result<u32, CoreError> {
    let rounded = (points * 20.0).round_ties_even();
    if !points.is_finite() || points < 0.0 || rounded > f64::from(u32::MAX) {
        return Err(CoreError::InvalidLayout(
            "points must be finite and fit in nonnegative twips",
        ));
    }
    Ok(rounded as u32)
}

pub fn twips_to_points(twips: u32) -> f64 {
    f64::from(twips) / 20.0
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TranslationEligibility {
    Eligible,
    EmptySource,
    VerbatimOnly,
    AlreadyTranslated,
    StaleSource,
}

pub fn translation_eligibility(
    source: &str,
    target: Option<&str>,
    expected_source_sha256: Option<&str>,
) -> TranslationEligibility {
    if expected_source_sha256.is_some_and(|hash| hash != crate::sha256_bytes(source.as_bytes())) {
        TranslationEligibility::StaleSource
    } else if source.trim().is_empty() {
        TranslationEligibility::EmptySource
    } else if crate::prose(source).trim().is_empty() {
        TranslationEligibility::VerbatimOnly
    } else if target.is_some_and(|text| !text.trim().is_empty()) {
        TranslationEligibility::AlreadyTranslated
    } else {
        TranslationEligibility::Eligible
    }
}
