// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! `$readmemh` files: whitespace-separated hex words, `@addr` (word index) directives
//! and `//` comments, as soc_ram's preload reads firmware.hex and firmware_d0-3.hex.

/// Words by index; indices a file skips over with `@` read as 0.
pub fn parse(text: &str) -> Result<Vec<u32>, String> {
    let mut words = Vec::new();
    let mut at = 0usize;
    for (n, line) in text.lines().enumerate() {
        let line = line.split("//").next().unwrap_or("");
        for tok in line.split_whitespace() {
            let (is_addr, digits) = match tok.strip_prefix('@') {
                Some(d) => (true, d),
                None => (false, tok),
            };
            let v = u64::from_str_radix(&digits.replace('_', ""), 16)
                .map_err(|e| format!("line {}: {tok:?}: {e}", n + 1))?;
            if is_addr {
                at = v as usize;
            } else {
                if words.len() <= at {
                    words.resize(at + 1, 0);
                }
                words[at] = v as u32;
                at += 1;
            }
        }
    }
    Ok(words)
}

#[cfg(test)]
mod tests {
    #[test]
    fn words_addresses_and_comments() {
        let w = super::parse("0000_0013 // nop\nDEADBEEF\n@4 1\n").unwrap();
        assert_eq!(w, [0x13, 0xDEAD_BEEF, 0, 0, 1]);
        assert!(super::parse("xyz").is_err());
    }
}
