VENV := .venv
SCENES := 1_fantascienza 2_giallo 3_western 4_bollywood 5_fantasy 6_azione

.PHONY: help $(SCENES)

help:
	@echo "Usage: make <scene> [ARGS='--simulate']"
	@echo "Scenes: $(SCENES)"

# Extra flags (e.g. --quiet, --simulate, a serial port) via ARGS:
#   make giallo ARGS='--simulate'
$(SCENES):
	. $(VENV)/bin/activate && python scene_runner.py scenes/$@.yaml $(ARGS)
