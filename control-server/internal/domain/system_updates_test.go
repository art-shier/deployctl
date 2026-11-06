package domain

import "testing"

func TestUpdateStableVersionOrderAndProtocol(t *testing.T) {
	for _, value := range []string{"v1.15.0", "v1.100.0", "v1.16.123"} {
		if !CompatibleUpdateVersion(value) {
			t.Errorf("reject %s", value)
		}
	}
	for _, value := range []string{"v1.14.9", "v2.0.0", "v1.15.0-rc.1", "1.15.0", "v01.15.0", "v1.15.0+meta", "v1.99999999999999999999.0"} {
		if CompatibleUpdateVersion(value) {
			t.Errorf("accept %s", value)
		}
	}
	if !UpdateVersionNewer("v1.100.0", "v1.99.9") || UpdateVersionNewer("v1.16.0", "v1.16.0") || UpdateVersionNewer("v1.15.0", "v1.16.0") {
		t.Fatal("numeric ordering")
	}
}
