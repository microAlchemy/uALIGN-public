use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "constraint")]
pub enum AlignConstraint {
    #[serde(rename = "SymmetricNets")]
    SymmetricNets {
        direction: String,
        net1: String,
        net2: String,
    },
    #[serde(rename = "SymmetricBlocks")]
    SymmetricBlocks {
        direction: String,
        pairs: Vec<Vec<String>>,
    },
    #[serde(rename = "PlaceCloser")]
    PlaceCloser {
        instance1: String,
        instance2: String,
    },
    #[serde(rename = "AspectRatio")]
    AspectRatio {
        ratio_low: f64,
        ratio_high: f64,
        #[serde(skip_serializing_if = "Option::is_none")]
        weight: Option<i32>,
    },
    #[serde(rename = "Boundary")]
    Boundary { max_width: f64, max_height: f64 },
}
