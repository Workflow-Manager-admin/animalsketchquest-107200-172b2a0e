#!/bin/bash
cd /home/kavia/workspace/code-generation/animalsketchquest-107200-172b2a0e/drawing_game_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

