package replacer

// Every-split check of the Buildkite agent log redactor (internal/replacer), run inside the
// package directory of a pinned checkout (see README.md). XD_FIXTURES, XD_OUT as for GitLab.

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func xdClassify(out, value string) string {
	if strings.Contains(out, value) {
		return "whole"
	}
	for k := 4; k < len(value); k++ {
		if strings.Contains(out, value[:k]) || strings.Contains(out, value[len(value)-k:]) {
			return "fragment"
		}
	}
	return "none"
}

func xdRun(value string, parts []string) string {
	var buf bytes.Buffer
	r := New(&buf, []string{value}, func([]byte) []byte { return []byte("[REDACTED]") })
	for _, p := range parts {
		_, _ = r.Write([]byte(p))
	}
	_ = r.Flush()
	return buf.String()
}

func TestXDEverySplit(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("XD_FIXTURES"))
	if err != nil {
		t.Skip("XD_FIXTURES not set")
	}
	var fx map[string]string
	_ = json.Unmarshal(raw, &fx)
	type row struct {
		Cut  int    `json:"cut_in_value"`
		Leak string `json:"leak"`
	}
	type caseReport struct {
		Name          string `json:"name"`
		ValueLength   int    `json:"value_length"`
		Whole         string `json:"whole"`
		SplitsTried   int    `json:"splits_tried"`
		LeakingSplits int    `json:"leaking_splits"`
		Rows          []row  `json:"rows"`
	}
	var cases []caseReport
	for _, name := range []string{"email", "ssn", "pem2048"} {
		value := fx[name]
		text := "header line one\nuser record: " + value + " ; trailing text\n"
		start := strings.Index(text, value)
		cr := caseReport{Name: name, ValueLength: len(value), Whole: xdClassify(xdRun(value, []string{text}), value)}
		for cut := start - 8; cut <= start+len(value)+8; cut++ {
			if cut < 1 || cut >= len(text) {
				continue
			}
			leak := xdClassify(xdRun(value, []string{text[:cut], text[cut:]}), value)
			cr.Rows = append(cr.Rows, row{Cut: cut - start, Leak: leak})
			cr.SplitsTried++
			if leak != "none" {
				cr.LeakingSplits++
			}
		}
		t.Logf("%s: whole=%s splits=%d leaking=%d", name, cr.Whole, cr.SplitsTried, cr.LeakingSplits)
		cases = append(cases, cr)
	}
	out, _ := json.MarshalIndent(map[string]interface{}{"target": "buildkite-agent replacer (internal/replacer)", "cases": cases}, "", " ")
	_ = os.WriteFile(os.Getenv("XD_OUT"), out, 0o644)
}
