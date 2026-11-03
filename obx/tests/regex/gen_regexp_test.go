// Copied into a copy of Go's regexp package by gen_go_tests.py: runs the
// package's own test tables (find_test.go, all_test.go, onepass_test.go;
// BSD-3-Clause, Copyright 2010 The Go Authors) through the real regexp and
// writes what Go produces, for obx's regex tests to compare with.

package regexp

import (
	"encoding/hex"
	"fmt"
	"os"
	"regexp/syntax"
	"strings"
	"testing"
)

func hx(s string) string { return hex.EncodeToString([]byte(s)) }

func ints(a []int) string {
	if a == nil {
		return "nil"
	}
	var b []string
	for _, x := range a {
		b = append(b, fmt.Sprint(x))
	}
	return strings.Join(b, ",")
}

func intss(a [][]int) string {
	if a == nil {
		return "nil"
	}
	var b []string
	for _, x := range a {
		b = append(b, ints(x))
	}
	return strings.Join(b, ";")
}

func strs(a []string) string {
	if a == nil {
		return "nil"
	}
	var b []string
	for _, x := range a {
		b = append(b, hx(x))
	}
	return fmt.Sprint(len(a)) + "[" + strings.Join(b, ",") + "]"
}

func TestGenObx(t *testing.T) {
	f, err := os.Create(os.Getenv("OBX_GEN_OUT"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w := func(kind string, fields ...string) {
		fmt.Fprintln(f, kind+"\t"+strings.Join(fields, "\t"))
	}
	for _, ft := range findTests {
		re := MustCompile(ft.pat)
		all := re.FindAllStringSubmatchIndex(ft.text, -1)
		re2 := MustCompile(ft.pat)
		re2.Longest()
		allLongest := re2.FindAllStringSubmatchIndex(ft.text, -1)
		w("find", hx(ft.pat), hx(ft.text), intss(all), intss(allLongest), fmt.Sprint(re.MatchString(ft.text)),
			fmt.Sprint(re.onepass != nil))
	}
	for _, s := range goodRe {
		_, err := Compile(s)
		w("good", hx(s), fmt.Sprint(err == nil))
	}
	for _, e := range badRe {
		_, err := Compile(e.re)
		msg := ""
		if err != nil {
			msg = err.Error()
		}
		w("bad", hx(e.re), hx(msg))
	}
	for _, list := range [][]ReplaceTest{replaceTests, replaceLiteralTests} {
		for _, tc := range list {
			re, err := Compile(tc.pattern)
			if err != nil {
				t.Fatal(err)
			}
			w("replace", hx(tc.pattern), hx(tc.replacement), hx(tc.input), hx(re.ReplaceAllString(tc.input, tc.replacement)),
				hx(re.ReplaceAllLiteralString(tc.input, tc.replacement)))
		}
	}
	for _, tc := range replaceFuncTests {
		re := MustCompile(tc.pattern)
		// All of Go's replace functions are func(s) { "x" + s + "y" }.
		w("replacefunc", hx(tc.pattern), hx(tc.input), hx(re.ReplaceAllStringFunc(tc.input, func(s string) string { return "x" + s + "y" })))
	}
	for _, tc := range append(metaTests, literalPrefixTests...) {
		re := MustCompile(tc.pattern)
		prefix, complete := re.LiteralPrefix()
		w("meta", hx(tc.pattern), hx(QuoteMeta(tc.pattern)), hx(prefix), fmt.Sprint(complete))
	}
	for _, c := range subexpCases {
		re := MustCompile(c.input)
		var idx []string
		for _, s := range c.indices {
			idx = append(idx, hx(s.name)+"="+fmt.Sprint(re.SubexpIndex(s.name)))
		}
		w("subexp", hx(c.input), fmt.Sprint(re.NumSubexp()), strs(re.SubexpNames()), strings.Join(idx, ","))
	}
	for _, tc := range splitTests {
		re := MustCompile(tc.r)
		w("split", hx(tc.s), hx(tc.r), fmt.Sprint(tc.n), strs(re.Split(tc.s, tc.n)))
	}
	for _, tc := range onePassTests {
		re, err := syntax.Parse(tc.re, syntax.Perl)
		if err != nil {
			t.Fatal(err)
		}
		p, err := syntax.Compile(re.Simplify())
		if err != nil {
			t.Fatal(err)
		}
		w("onepass", hx(tc.re), fmt.Sprint(compileOnePass(p) != nil))
	}
	for _, tc := range onePassTests1 {
		re := MustCompile(tc.re)
		w("onepassrun", hx(tc.re), hx(tc.match), fmt.Sprint(re.onepass != nil), fmt.Sprint(re.MatchString(tc.match)))
	}
	for _, tc := range minInputLenTests {
		re, err := syntax.Parse(tc.Regexp, syntax.Perl)
		if err != nil {
			t.Fatal(err)
		}
		w("mininputlen", hx(tc.Regexp), fmt.Sprint(minInputLen(re)))
	}
}
