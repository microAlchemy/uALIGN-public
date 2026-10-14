use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fmt::Debug;

// ============================================================================
// Trait Hierarchy (mirrors align/schema/constraint.py)
// SoftConstraint <-- HardConstraint <-- UserConstraint
// ============================================================================

/// Handles schema identity (`"constraint": "<Name>"`) and JSON serialization.
pub trait SoftConstraint: Debug + Send + Sync {
    /// Canonical ALIGN constraint name (e.g. `"SymmetricNets"`).
    fn constraint_name(&self) -> &'static str;

    /// Serialize this constraint to a JSON object with `"constraint"` injected.
    fn to_json(&self) -> Value;

    /// Optional lower-level companion constraints (e.g. `DoNotIdentify`).
    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        Vec::new()
    }
}

/// Geometric constraints checked for feasibility before PnR
pub trait HardConstraint: SoftConstraint {
    /// Validate internal bounds and geometric self-consistency.
    fn validate(&self) -> Result<()>;
}

/// High-level composite constraints that expand into lower-level Hard/Soft constraints
pub trait UserConstraint: HardConstraint {
    /// Expand this macro constraint into primitive `HardConstraint` / `SoftConstraint` items.
    fn expand(&self) -> Vec<Box<dyn SoftConstraint>>;
}

/// Helper to inject `"constraint": <name>` into a serialized struct's JSON map.
fn serialize_with_tag<T: Serialize>(name: &'static str, value: &T) -> Value {
    let mut map = match serde_json::to_value(value).unwrap_or_else(|_| json!({})) {
        Value::Object(m) => m,
        _ => serde_json::Map::new(),
    };
    map.insert("constraint".to_string(), Value::String(name.to_string()));
    Value::Object(map)
}

// ============================================================================
// Soft Constraints (Routing / Clustering / Compiler Hints)
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymmetricNets {
    pub direction: String,
    pub net1: String,
    pub net2: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pins1: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pins2: Option<Vec<String>>,
}

impl SoftConstraint for SymmetricNets {
    fn constraint_name(&self) -> &'static str {
        "SymmetricNets"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaceCloser {
    pub instances: Vec<String>,
}

impl SoftConstraint for PlaceCloser {
    fn constraint_name(&self) -> &'static str {
        "PlaceCloser"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DoNotIdentify {
    pub instances: Vec<String>,
}

impl SoftConstraint for DoNotIdentify {
    fn constraint_name(&self) -> &'static str {
        "DoNotIdentify"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }
}

// ============================================================================
// Hard Constraints (SMT / Geometric Placement Constraints)
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Order {
    pub instances: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub direction: Option<String>,
    #[serde(default)]
    pub abut: bool,
}

impl SoftConstraint for Order {
    fn constraint_name(&self) -> &'static str {
        "Order"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }

    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        vec![Box::new(DoNotIdentify {
            instances: self.instances.clone(),
        })]
    }
}

impl HardConstraint for Order {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.instances.len() >= 2,
            "Order must contain at least two instances"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Align {
    pub instances: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line: Option<String>,
}

impl SoftConstraint for Align {
    fn constraint_name(&self) -> &'static str {
        "Align"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }

    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        vec![Box::new(DoNotIdentify {
            instances: self.instances.clone(),
        })]
    }
}

impl HardConstraint for Align {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.instances.len() >= 2,
            "Align must contain at least two instances"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymmetricBlocks {
    pub direction: String,
    pub pairs: Vec<Vec<String>>,
}

impl SoftConstraint for SymmetricBlocks {
    fn constraint_name(&self) -> &'static str {
        "SymmetricBlocks"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }

    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        let instances = self.pairs.iter().flatten().cloned().collect();
        vec![Box::new(DoNotIdentify { instances })]
    }
}

impl HardConstraint for SymmetricBlocks {
    fn validate(&self) -> Result<()> {
        ensure!(
            !self.pairs.is_empty(),
            "SymmetricBlocks must contain at least one pair"
        );
        for pair in &self.pairs {
            ensure!(
                (1..=2).contains(&pair.len()),
                "Each SymmetricBlocks entry must have 1 or 2 instances"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AspectRatio {
    pub ratio_low: f64,
    pub ratio_high: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub weight: Option<i32>,
}

impl SoftConstraint for AspectRatio {
    fn constraint_name(&self) -> &'static str {
        "AspectRatio"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }
}

impl HardConstraint for AspectRatio {
    fn validate(&self) -> Result<()> {
        ensure!(self.ratio_low >= 0.0, "AspectRatio ratio_low must be >= 0");
        ensure!(
            self.ratio_high > self.ratio_low,
            "AspectRatio ratio_high must be greater than ratio_low"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Boundary {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_width: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_height: Option<f64>,
}

impl SoftConstraint for Boundary {
    fn constraint_name(&self) -> &'static str {
        "Boundary"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }
}

impl HardConstraint for Boundary {
    fn validate(&self) -> Result<()> {
        if let Some(w) = self.max_width {
            ensure!(w > 0.0, "Boundary max_width must be positive");
        }
        if let Some(h) = self.max_height {
            ensure!(h > 0.0, "Boundary max_height must be positive");
        }
        Ok(())
    }
}

// ============================================================================
// User Constraints (High-Level Composite Macros)
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlignInOrder {
    pub instances: Vec<String>,
    pub line: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub direction: Option<String>,
    #[serde(default)]
    pub abut: bool,
}

impl SoftConstraint for AlignInOrder {
    fn constraint_name(&self) -> &'static str {
        "AlignInOrder"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }

    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        self.expand()
    }
}

impl HardConstraint for AlignInOrder {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.instances.len() >= 2,
            "AlignInOrder must contain at least two instances"
        );
        Ok(())
    }
}

impl UserConstraint for AlignInOrder {
    fn expand(&self) -> Vec<Box<dyn SoftConstraint>> {
        let resolved_dir = self
            .direction
            .as_deref()
            .unwrap_or(match self.line.as_str() {
                "left" | "right" => "vertical",
                _ => "horizontal",
            });
        let prefix = &resolved_dir[0..1];
        let order_dir = if resolved_dir == "horizontal" {
            "left_to_right"
        } else {
            "top_to_bottom"
        };

        vec![
            Box::new(Align {
                instances: self.instances.clone(),
                line: Some(format!("{}_{}", prefix, self.line)),
            }),
            Box::new(Order {
                instances: self.instances.clone(),
                direction: Some(order_dir.to_string()),
                abut: self.abut,
            }),
            Box::new(DoNotIdentify {
                instances: self.instances.clone(),
            }),
        ]
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Floorplan {
    pub regions: Vec<Vec<String>>,
    #[serde(default)]
    pub order: bool,
    #[serde(default)]
    pub symmetrize: bool,
}

impl SoftConstraint for Floorplan {
    fn constraint_name(&self) -> &'static str {
        "Floorplan"
    }

    fn to_json(&self) -> Value {
        serialize_with_tag(self.constraint_name(), self)
    }

    fn yield_constraints(&self) -> Vec<Box<dyn SoftConstraint>> {
        self.expand()
    }
}

impl HardConstraint for Floorplan {
    fn validate(&self) -> Result<()> {
        ensure!(
            !self.regions.is_empty(),
            "Floorplan must contain at least one region row"
        );
        Ok(())
    }
}

impl UserConstraint for Floorplan {
    fn expand(&self) -> Vec<Box<dyn SoftConstraint>> {
        let mut expanded: Vec<Box<dyn SoftConstraint>> = Vec::new();

        for i in 0..self.regions.len().saturating_sub(1) {
            for above in &self.regions[i] {
                for below in &self.regions[i + 1] {
                    expanded.push(Box::new(Order {
                        instances: vec![above.clone(), below.clone()],
                        direction: Some("top_to_bottom".to_string()),
                        abut: false,
                    }));
                }
            }
        }

        if self.order {
            for region in &self.regions {
                if region.len() > 1 {
                    expanded.push(Box::new(Order {
                        instances: region.clone(),
                        direction: Some("left_to_right".to_string()),
                        abut: false,
                    }));
                }
            }
        }

        if self.symmetrize {
            let mut pairs = Vec::new();
            for region in &self.regions {
                if region.len() <= 2 {
                    pairs.push(region.clone());
                } else {
                    let half = region.len() / 2;
                    for i in 0..half {
                        pairs.push(vec![
                            region[i].clone(),
                            region[region.len() - 1 - i].clone(),
                        ]);
                    }
                    if region.len() % 2 == 1 {
                        pairs.push(vec![region[half].clone()]);
                    }
                }
            }
            expanded.push(Box::new(SymmetricBlocks {
                direction: "V".to_string(),
                pairs,
            }));
        }

        if self.symmetrize && self.order {
            let instances = self.regions.iter().flatten().cloned().collect();
            expanded.push(Box::new(DoNotIdentify { instances }));
        }

        expanded
    }
}
