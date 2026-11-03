// The regex benchmark, Go's driver: the work of regex_bench.obx with Go's
// regexp.
//
// usage: main CORPUS [REPS]
package main

import (
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var names = []string{"literal", "alternation", "words", "nocase", "email", "errors", "dates", "hostile"}
var patterns = []string{
	`Sherlock Holmes`,
	`Sherlock|Holmes|Watson|Irene`,
	`\w+`,
	`(?i)sherlock`,
	`[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}`,
	`(?m)^ERROR .*timeout$`,
	`(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})`,
	`(a*)*b`,
}

func work(re *regexp.Regexp, k int, text string) int {
	switch k {
	case 6:
		n := 0
		y := re.SubexpIndex("y")
		for _, m := range re.FindAllStringSubmatchIndex(text, -1) {
			n += 1 + (m[2*y+1] - m[2*y]) - 4
		}
		return n
	case 7:
		if re.MatchString(text) {
			return 1
		}
		return 0
	}
	return len(re.FindAllStringIndex(text, -1))
}

func main() {
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	corpus := string(data)
	reps := 5
	if len(os.Args) > 2 {
		reps, _ = strconv.Atoi(os.Args[2])
	}
	hostile := strings.Repeat("a", 100000)
	for k := range names {
		re := regexp.MustCompile(patterns[k])
		text := corpus
		if k == 7 {
			text = hostile
		}
		var best time.Duration
		count := 0
		for r := 0; r < reps; r++ {
			t0 := time.Now()
			count = work(re, k, text)
			t := time.Since(t0)
			if r == 0 || t < best {
				best = t
			}
		}
		fmt.Printf("%s %d %.2f\n", names[k], count, float64(best.Nanoseconds())/1e6)
	}
}
