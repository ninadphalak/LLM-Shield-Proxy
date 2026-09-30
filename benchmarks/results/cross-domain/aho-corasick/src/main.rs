//! Every-split check of aho_corasick::AhoCorasick::try_stream_replace_all with a reader that
//! hands the stream over in exactly two reads cut at every offset.
//! Usage: xd-aho <fixtures.json> <out.json>
use aho_corasick::AhoCorasick;
use std::io::{self, Read};

struct TwoPart {
    parts: Vec<Vec<u8>>,
    idx: usize,
}

impl Read for TwoPart {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        // one whole part per read call, so the boundary is exactly the cut
        if self.idx >= self.parts.len() {
            return Ok(0);
        }
        let p = &self.parts[self.idx];
        assert!(buf.len() >= p.len(), "read buffer smaller than a part");
        buf[..p.len()].copy_from_slice(p);
        self.idx += 1;
        Ok(p.len())
    }
}

fn classify(out: &str, value: &str) -> &'static str {
    if out.contains(value) {
        return "whole";
    }
    for k in 4..value.len() {
        if out.contains(&value[..k]) || out.contains(&value[value.len() - k..]) {
            return "fragment";
        }
    }
    "none"
}

fn run(value: &str, parts: Vec<Vec<u8>>) -> String {
    let ac = AhoCorasick::new([value]).unwrap();
    let mut out = Vec::new();
    ac.try_stream_replace_all(TwoPart { parts, idx: 0 }, &mut out, &["[REDACTED]"]).unwrap();
    String::from_utf8_lossy(&out).into_owned()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let fx: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&args[1]).unwrap()).unwrap();
    let mut cases = Vec::new();
    for name in ["email", "ssn", "pem2048"] {
        let value = fx[name].as_str().unwrap();
        let text = format!("header line one\nuser record: {} ; trailing text\n", value);
        let start = text.find(value).unwrap();
        let whole = classify(&run(value, vec![text.as_bytes().to_vec()]), value);
        let mut rows = Vec::new();
        let mut leaking = 0;
        for cut in start.saturating_sub(8).max(1)..=(start + value.len() + 8).min(text.len() - 1) {
            let parts = vec![text.as_bytes()[..cut].to_vec(), text.as_bytes()[cut..].to_vec()];
            let leak = classify(&run(value, parts), value);
            if leak != "none" {
                leaking += 1;
            }
            rows.push(serde_json::json!({"cut_in_value": cut as i64 - start as i64, "leak": leak}));
        }
        println!("{}: whole={} splits={} leaking={}", name, whole, rows.len(), leaking);
        cases.push(serde_json::json!({"name": name, "value_length": value.len(), "whole": whole,
            "splits_tried": rows.len(), "leaking_splits": leaking, "rows": rows}));
    }
    let report = serde_json::json!({"target": "aho-corasick try_stream_replace_all", "cases": cases});
    std::fs::write(&args[2], serde_json::to_string_pretty(&report).unwrap()).unwrap();
}
