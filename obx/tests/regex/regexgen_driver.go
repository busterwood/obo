// The Go side of obx/tests/regexgen.py: reads the generated cases (lines
// "P\tHEX" for a pattern, then "T\tHEX" for each text) and writes what Go's
// regexp does with them, in the format regexgen_driver.obx writes:
//
//	P HEX               the pattern
//	E HEX               its compile error, or
//	T HEX FIRST LONGEST REPLACE SPLIT
//	                    per text: all submatch indexes, leftmost-first and
//	                    leftmost-longest; ReplaceAllString with the template
//	                    "<$1|${n0}|$0>"; Split(text, -1)
//
// usage: go run regexgen_driver.go CASES
package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"os"
	"regexp"
	"strings"
)

func ints(a []int) string {
	var b []string
	for _, x := range a {
		b = append(b, fmt.Sprint(x))
	}
	return strings.Join(b, ",")
}

func all(m [][]int) string {
	if len(m) == 0 {
		return "-"
	}
	var b []string
	for _, x := range m {
		b = append(b, ints(x))
	}
	return strings.Join(b, ";")
}

func hx(s string) string { return "h" + hex.EncodeToString([]byte(s)) }

func main() {
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	var re, rel *regexp.Regexp
	for _, line := range strings.Split(string(data), "\n") {
		if line == "" {
			continue
		}
		raw, err := hex.DecodeString(line[2:])
		if err != nil {
			panic(err)
		}
		s := string(raw)
		if line[0] == 'P' {
			fmt.Fprintf(w, "P %s\n", hx(s))
			re, err = regexp.Compile(s)
			if err != nil {
				fmt.Fprintf(w, "E %s\n", hx(err.Error()))
				re = nil
				continue
			}
			rel = regexp.MustCompile(s)
			rel.Longest()
			continue
		}
		if re == nil {
			continue
		}
		var sp []string
		for _, p := range re.Split(s, -1) {
			sp = append(sp, hx(p))
		}
		fmt.Fprintf(w, "T %s %s %s %s %s\n", hx(s), all(re.FindAllStringSubmatchIndex(s, -1)),
			all(rel.FindAllStringSubmatchIndex(s, -1)), hx(re.ReplaceAllString(s, "<$1|${n0}|$0>")), strings.Join(sp, ","))
	}
}
