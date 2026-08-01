VENV := .venv
SCENES := anime azione bollywood fantascienza giallo rfid western

.PHONY: help $(SCENES)

help:
	@echo "Usage: make <scene> [ARGS='--simulate']"
	@echo "Scenes: $(SCENES)"

# Extra flags (e.g. --quiet, --simulate, a serial port) via ARGS:
#   make giallo ARGS='--simulate'
$(SCENES):
	. $(VENV)/bin/activate && python scene_runner.py scenes/$@.yaml $(ARGS)
