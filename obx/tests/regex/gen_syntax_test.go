// Copied into a copy of Go's regexp/syntax package by gen_go_tests.py: runs
// the package's own test tables through the real parser, simplifier and
// compiler and writes what Go produces, for obx's regex tests to compare
// with. Uses the tables and the dump helper of Go's parse_test.go,
// simplify_test.go and prog_test.go (BSD-3-Clause, Copyright 2011 The Go
// Authors).

package syntax

import (
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"testing"
)

func hx(s string) string { return hex.EncodeToString([]byte(s)) }

func TestGenObx(t *testing.T) {
	f, err := os.Create(os.Getenv("OBX_GEN_OUT"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w := func(kind string, fields ...string) {
		fmt.Fprintln(f, kind+"\t"+strings.Join(fields, "\t"))
	}
	parse := func(pat string, flags Flags) {
		re, err := Parse(pat, flags)
		out := ""
		if err != nil {
			e := err.(*Error)
			out = "!" + string(e.Code) + "\x00" + e.Expr
		} else {
			out = dump(re)
		}
		w("parse", hx(pat), fmt.Sprint(int(flags)), hx(out))
	}
	for _, tt := range parseTests {
		parse(tt.Regexp, testFlags)
	}
	for _, tt := range foldcaseTests {
		parse(tt.Regexp, FoldCase)
	}
	for _, tt := range literalTests {
		parse(tt.Regexp, Literal)
	}
	for _, tt := range matchnlTests {
		parse(tt.Regexp, MatchNL)
	}
	for _, tt := range nomatchnlTests {
		parse(tt.Regexp, 0)
	}
	for _, list := range [][]string{invalidRegexps, onlyPerl, onlyPOSIX} {
		for _, s := range list {
			parse(s, Perl)
			parse(s, POSIX)
		}
	}
	// String(): the parse tests and Go's string tests.
	str := func(pat string, flags Flags) {
		re, err := Parse(pat, flags)
		if err != nil {
			return
		}
		w("string", hx(pat), fmt.Sprint(int(flags)), hx(re.String()))
	}
	for _, tt := range parseTests {
		str(tt.Regexp, testFlags)
	}
	for _, tt := range stringTests {
		str(tt.re, Perl)
	}
	// Simplify().String().
	for _, tt := range simplifyTests {
		flags := MatchNL | Perl&^OneLine
		re, err := Parse(tt.Regexp, flags)
		if err != nil {
			t.Fatal(err)
		}
		w("simplify", hx(tt.Regexp), fmt.Sprint(int(flags)), hx(re.Simplify().String()))
	}
	// Prog.String() of the unsimplified tree.
	for _, tt := range compileTests {
		re, _ := Parse(tt.Regexp, Perl)
		p, _ := Compile(re)
		w("compile", hx(tt.Regexp), fmt.Sprint(int(Perl)), hx(p.String()))
	}
}
