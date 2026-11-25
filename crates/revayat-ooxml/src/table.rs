use crate::{
    OoxmlError,
    opc::{Relationship, W, child},
    reader::{Intake, block_id, measure, on},
};
use revayat_core::{Block, TableCell, TableRow};
use roxmltree::Node;
use std::collections::BTreeMap;
impl Intake<'_> {
    pub(crate) fn table(
        &mut self,
        n: Node<'_, '_>,
        rels: &BTreeMap<String, Relationship>,
    ) -> Result<Block, OoxmlError> {
        let mut rows: Vec<TableRow> = Vec::new();
        let mut active: BTreeMap<u32, (usize, usize, u32)> = BTreeMap::new();
        for row in n.children().filter(|c| c.has_tag_name((W, "tr"))) {
            let mut output = TableRow {
                header: child(row, W, "trPr")
                    .and_then(|p| child(p, W, "tblHeader"))
                    .is_some_and(on),
                cells: Vec::new(),
            };
            let mut next = BTreeMap::new();
            let mut col = 0_u32;
            for cell in row.children().filter(|c| c.has_tag_name((W, "tc"))) {
                let prop = child(cell, W, "tcPr");
                let span = prop
                    .and_then(|p| child(p, W, "gridSpan"))
                    .map_or(Ok(1), |p| measure(p, "val"))?;
                col = col
                    .checked_add(span)
                    .filter(|n| *n <= 256 && span > 0)
                    .ok_or_else(|| OoxmlError::Limit("table columns".into()))?;
                let blocks = self.blocks(cell, rels)?;
                if let Some(v) = prop.and_then(|p| child(p, W, "vMerge")) {
                    if v.attribute((W, "val")) == Some("restart") {
                        next.insert(col - span, (rows.len(), output.cells.len(), span));
                    } else {
                        let key = col - span;
                        let &(ri, ci, previous_span) = active
                            .get(&key)
                            .filter(|(_, _, s)| *s == span)
                            .ok_or_else(|| {
                                OoxmlError::Package(
                                    "vertical span without a matching origin".into(),
                                )
                            })?;
                        if blocks
                            .iter()
                            .any(|b| !matches!(b,Block::Paragraph{runs,..} if runs.is_empty()))
                        {
                            return Err(OoxmlError::Unsupported(
                                "authored content in a merged continuation cell".into(),
                            ));
                        }
                        let start = &mut rows[ri].cells[ci];
                        start.row_span = Some(start.row_span.unwrap_or(1) + 1);
                        next.insert(key, (ri, ci, previous_span));
                        continue;
                    }
                }
                output.cells.push(TableCell {
                    blocks,
                    grid_span: (span > 1).then_some(span),
                    row_span: None,
                });
            }
            rows.push(output);
            active = next;
        }
        Ok(Block::Table {
            id: block_id(n, 0),
            rows,
        })
    }
}
