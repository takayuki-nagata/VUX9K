// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::shell: the second stage's command line (typing, splitting,
//! the working directory's path).

extern crate std;
use std::string::String;
use std::vec::Vec;

use fw_common::shell::{Args, Key, LineBuf, Path, ARGS_MAX, LINE_MAX, PATH_MAX};

/// Feed `input`; the echo it makes (backspace as `<`) and the lines it ends.
fn typed(input: &[u8]) -> (String, Vec<String>) {
    let mut lb = LineBuf::default();
    let mut echo = String::new();
    let mut lines = Vec::new();
    for &b in input {
        match lb.feed(b) {
            Key::Char(c) => echo.push(c as char),
            Key::Erase => echo.push('<'),
            Key::Enter => {
                lines.push(String::from_utf8(lb.line().to_vec()).unwrap());
                lb.clear();
            }
            Key::Ignored => {}
        }
    }
    (echo, lines)
}

#[test]
fn lines_are_typed_with_backspace() {
    assert_eq!(typed(b"ls\r"), ("ls".into(), std::vec!["ls".into()]));
    // Backspace and DEL, also on an empty line (ignored); line feeds and controls dropped
    assert_eq!(
        typed(b"\x08cx\x08d\x7f\x7fls\n\t\x1b\r\r"),
        ("cx<d<<ls".into(), std::vec!["ls".into(), "".into()])
    );
    // A full line takes no more
    let mut long = std::vec![b'a'; LINE_MAX + 5];
    long.push(b'\r');
    let (echo, lines) = typed(&long);
    assert_eq!(echo.len(), LINE_MAX);
    assert_eq!(lines[0].len(), LINE_MAX);
    // ... but a backspace still works there
    let mut full = std::vec![b'a'; LINE_MAX];
    full.extend_from_slice(b"\x08b\r");
    let (_, lines) = typed(&full);
    assert_eq!(lines[0].len(), LINE_MAX);
    assert!(lines[0].ends_with("ab"));
    // Bytes above 0x7E are not ASCII
    assert_eq!(typed(b"a\xe9\xffb\r").1, std::vec![String::from("ab")]);
}

#[test]
fn lines_split_into_command_and_arguments() {
    let a = Args::parse(b"  mv \tA.TXT   DIR/B.TXT ");
    assert_eq!(a.cmd, b"mv");
    assert_eq!(a.args(), [&b"A.TXT"[..], b"DIR/B.TXT"]);
    assert!(!a.extra);
    assert_eq!(a.exactly(2), Some(&[&b"A.TXT"[..], b"DIR/B.TXT"][..]));
    assert_eq!(a.exactly(1), None);
    let a = Args::parse(b"");
    assert_eq!((a.cmd, a.args().len(), a.extra), (&b""[..], 0, false));
    assert_eq!(a.exactly(0), Some(&[][..]));
    let a = Args::parse(b"   ");
    assert_eq!(a.cmd, b"");
    let a = Args::parse(b"ls");
    assert_eq!((a.cmd, a.args().len()), (&b"ls"[..], 0));
    let a = Args::parse(b"mv a b c");
    assert_eq!(a.args().len(), ARGS_MAX);
    assert!(a.extra);
    assert_eq!(a.exactly(2), None);
}

fn cd(from: &Path, arg: &str) -> Option<String> {
    from.join(arg.as_bytes())
        .map(|p| String::from_utf8(p.as_bytes().to_vec()).unwrap())
}

#[test]
fn paths_follow_cd() {
    let root = Path::default();
    assert_eq!(root.as_bytes(), b"/");
    assert_eq!(cd(&root, "VUX9K").as_deref(), Some("/VUX9K"));
    assert_eq!(cd(&root, "VUX9K/APPS/").as_deref(), Some("/VUX9K/APPS"));
    assert_eq!(cd(&root, "\\VUX9K\\APPS").as_deref(), Some("/VUX9K/APPS"));
    assert_eq!(cd(&root, "..").as_deref(), Some("/"));
    assert_eq!(cd(&root, "./A/./B/../C").as_deref(), Some("/A/C"));
    let apps = root.join(b"VUX9K/APPS").unwrap();
    assert_eq!(cd(&apps, "..").as_deref(), Some("/VUX9K"));
    assert_eq!(cd(&apps, "../..").as_deref(), Some("/"));
    assert_eq!(cd(&apps, "../../../X").as_deref(), Some("/X"));
    assert_eq!(cd(&apps, "/").as_deref(), Some("/"));
    assert_eq!(cd(&apps, "/TMP").as_deref(), Some("/TMP"));
    assert_eq!(cd(&apps, "").as_deref(), Some("/VUX9K/APPS"));
    assert_eq!(
        cd(&apps, "Long Name").as_deref(),
        Some("/VUX9K/APPS/Long Name")
    );
    // Up to PATH_MAX, not past it (and the path stays as it was)
    let name = "N".repeat(PATH_MAX - 1);
    assert_eq!(cd(&root, &name).map(|p| p.len()), Some(PATH_MAX));
    assert_eq!(cd(&root, &std::format!("{name}N")), None);
    let deep = root.join(name.as_bytes()).unwrap();
    assert_eq!(cd(&deep, "X"), None);
    assert_eq!(cd(&deep, "..").as_deref(), Some("/"));
}
