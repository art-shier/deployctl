package httpapi

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDecodeRequiresObject(t *testing.T) {
	for _, raw := range []string{"null", "[]", "{} {}"} {
		r := httptest.NewRequest("POST", "/", strings.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		var body struct{}
		if decode(httptest.NewRecorder(), r, &body) == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}
