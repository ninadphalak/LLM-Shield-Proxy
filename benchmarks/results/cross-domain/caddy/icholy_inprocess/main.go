// Every-split check of icholy/replace v0.6.0 (the library behind Caddy replace-response) driven
// directly through golang.org/x/text/transform.Writer with MaxMatchSize = 2048, the value Caddy
// sets. Usage: go run . <fixtures.json> <out.json>
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"

	"github.com/icholy/replace"
	"golang.org/x/text/transform"
)

var pem = regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\r\n]+-----END [A-Z ]*PRIVATE KEY-----`)

func classify(out, value string) string {
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

func run(parts []string) string {
	tr := replace.RegexpIndexFunc(pem, func([]byte, []int) []byte { return []byte("[REDACTED]") })
	tr.MaxMatchSize = 2048
	var buf bytes.Buffer
	w := transform.NewWriter(&buf, tr)
	for _, p := range parts {
		_, _ = w.Write([]byte(p))
	}
	_ = w.Close()
	return buf.String()
}

func main() {
	raw, _ := os.ReadFile(os.Args[1])
	var fx map[string]string
	_ = json.Unmarshal(raw, &fx)
	type row struct {
		Cut  int    `json:"cut_in_value"`
		Leak string `json:"leak"`
	}
	var cases []map[string]interface{}
	for _, name := range []string{"pem2048", "pem4096"} {
		value := fx[name]
		text := "header line one\nuser record: " + value + " ; trailing text\n"
		start := strings.Index(text, value)
		rows := []row{}
		leaking, first := 0, -1
		for cut := start - 8; cut <= start+len(value)+8; cut++ {
			if cut < 1 || cut >= len(text) {
				continue
			}
			leak := classify(run([]string{text[:cut], text[cut:]}), value)
			rows = append(rows, row{cut - start, leak})
			if leak != "none" {
				leaking++
				if first < 0 {
					first = cut - start
				}
			}
		}
		fmt.Printf("%s: whole=%s splits=%d leaking=%d first_leaking_cut=%d\n", name, classify(run([]string{text}), value), len(rows), leaking, first)
		cases = append(cases, map[string]interface{}{"name": name, "value_length": len(value), "splits_tried": len(rows), "leaking_splits": leaking, "first_leaking_cut_in_value": first, "rows": rows})
	}
	out, _ := json.MarshalIndent(map[string]interface{}{"target": "icholy/replace v0.6.0 RegexpTransformer via transform.Writer, MaxMatchSize 2048", "cases": cases}, "", " ")
	_ = os.WriteFile(os.Args[2], out, 0o644)
}
