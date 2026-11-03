// The regex benchmark, Rust's driver: the work of regex_bench.obx with the
// regex crate.
//
// usage: regex_bench CORPUS [REPS]
use regex::Regex;
use std::time::{Duration, Instant};

const NAMES: [&str; 8] = ["literal", "alternation", "words", "nocase", "email", "errors", "dates", "hostile"];
const PATTERNS: [&str; 8] = [
    r"Sherlock Holmes",
    r"Sherlock|Holmes|Watson|Irene",
    r"\w+",
    r"(?i)sherlock",
    r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
    r"(?m)^ERROR .*timeout$",
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})",
    r"(a*)*b",
];

fn work(re: &Regex, k: usize, text: &str) -> usize {
    match k {
        6 => {
            let mut n = 0;
            for c in re.captures_iter(text) {
                n += 1 + c.name("y").map_or(4, |m| m.len()) - 4;
            }
            n
        }
        7 => re.is_match(text) as usize,
        _ => re.find_iter(text).count(),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let corpus = std::fs::read_to_string(&args[1]).unwrap();
    let reps: usize = if args.len() > 2 { args[2].parse().unwrap() } else { 5 };
    let hostile = "a".repeat(100000);
    for k in 0..NAMES.len() {
        let re = Regex::new(PATTERNS[k]).unwrap();
        let text = if k == 7 { &hostile } else { &corpus };
        let mut best = Duration::MAX;
        let mut count = 0;
        for _ in 0..reps {
            let t0 = Instant::now();
            count = work(&re, k, text);
            best = best.min(t0.elapsed());
        }
        println!("{} {} {:.2}", NAMES[k], count, best.as_secs_f64() * 1e3);
    }
}
