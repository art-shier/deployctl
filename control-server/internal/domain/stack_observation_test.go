package domain

import "testing"

func TestStackObservationValidation(t *testing.T) {
	s := StackObservation{Services: map[string]ServiceObservation{"postgres": {Version: "v1.0.0", Image: "registry.example/db@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Lifecycle: "persistent", Health: "healthy"}}, Jobs: map[string]string{"restore": "succeeded"}}
	if err := s.Validate(); err != nil {
		t.Fatal(err)
	}
	s.OperationID = "../private"
	if s.Validate() == nil {
		t.Fatal("accepted invalid operation identity")
	}
	s.OperationID = ""
	s.Jobs["restore"] = "arbitrary secret value"
	if s.Validate() == nil {
		t.Fatal("accepted unbounded job state")
	}
}
