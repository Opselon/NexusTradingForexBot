// Package handlers tests the pagination validation contract against the
// FastAPI/Pydantic v2 behaviour captured in phase_b_contracts.json.
package handlers

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func req(q string) *http.Request {
	return httptest.NewRequest(http.MethodGet, "/api/v1/research/runs?"+q, nil)
}

// TestPaginationAcceptsValid covers the documented defaults and in-range
// values, which must never 422.
func TestPaginationAcceptsValid(t *testing.T) {
	cases := map[string]struct{ page, pageSize int }{
		"":                    {1, 50},
		"page=1":              {1, 50},
		"page=10":             {10, 50},
		"page=10000":          {10000, 50},
		"page_size=1":         {1, 1},
		"page_size=200":       {1, 200},
		"page=3&page_size=25": {3, 25},
	}
	for q, want := range cases {
		t.Run("valid:"+q, func(t *testing.T) {
			w := httptest.NewRecorder()
			page, ps, ok := validatePagination(w, req(q))
			if !ok {
				t.Fatalf("rejected valid query %q with status %d", q, w.Code)
			}
			if page != want.page || ps != want.pageSize {
				t.Errorf("%q -> page=%d pageSize=%d, want %d/%d", q, page, ps,
					want.page, want.pageSize)
			}
		})
	}
}

// TestPaginationRejects pins each Pydantic issue code. Conflating
// int_parsing with a range error is a real client-visible regression.
func TestPaginationRejects(t *testing.T) {
	cases := []struct {
		query string
		field string
		issue string
	}{
		{"page=0", "page", "greater_than_equal"},
		{"page=-5", "page", "greater_than_equal"},
		{"page=10001", "page", "less_than_equal"},
		{"page=abc", "page", "int_parsing"},
		{"page_size=0", "page_size", "greater_than_equal"},
		{"page_size=201", "page_size", "less_than_equal"},
		{"page_size=xyz", "page_size", "int_parsing"},
	}
	for _, tc := range cases {
		t.Run(tc.query, func(t *testing.T) {
			w := httptest.NewRecorder()
			if _, _, ok := validatePagination(w, req(tc.query)); ok {
				t.Fatalf("accepted invalid query %q", tc.query)
			}
			if w.Code != http.StatusUnprocessableEntity {
				t.Fatalf("status = %d, want 422", w.Code)
			}
			body := w.Body.String()
			if !strings.Contains(body, `"field":"`+tc.field+`"`) {
				t.Errorf("body lacks field %q: %s", tc.field, body)
			}
			if !strings.Contains(body, `"issue":"`+tc.issue+`"`) {
				t.Errorf("body lacks issue %q: %s", tc.issue, body)
			}
			if !strings.Contains(body, `"input_present":true`) {
				t.Errorf("input_present must be true: %s", body)
			}
			if !strings.Contains(body, `"VALIDATION_ERROR"`) {
				t.Errorf("envelope code missing: %s", body)
			}
		})
	}
}

// TestPaginationDoesNotEchoInput — the raw supplied value must never appear
// in the response (it is attacker-controlled, and FastAPI deliberately
// reports only input_present).
func TestPaginationDoesNotEchoInput(t *testing.T) {
	for _, q := range []string{"page=abc", "page_size=99999", "page=<script>"} {
		w := httptest.NewRecorder()
		validatePagination(w, req(q))
		if strings.Contains(w.Body.String(), "<script>") {
			t.Errorf("reflected input for %q: %s", q, w.Body.String())
		}
	}
}

// TestPaginationMultipleIssues — two bad params report BOTH errors, in the
// order they were validated (page then page_size), matching FastAPI.
func TestPaginationMultipleIssues(t *testing.T) {
	w := httptest.NewRecorder()
	validatePagination(w, req("page=abc&page_size=999"))
	body := w.Body.String()
	if strings.Count(body, `"field":"page"`) != 1 ||
		strings.Count(body, `"field":"page_size"`) != 1 {
		t.Errorf("expected both fields reported: %s", body)
	}
	idxPage := strings.Index(body, `"field":"page"`)
	idxPageSize := strings.Index(body, `"field":"page_size"`)
	if idxPage == -1 || idxPageSize == -1 || idxPage > idxPageSize {
		t.Errorf("issue order wrong: %s", body)
	}
}
