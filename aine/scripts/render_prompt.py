#!/usr/bin/env python3
"""Render a prompt template: replaces {{NAME}} with the environment variable NAME.

Usage: render_prompt.py aine/prompts/specify-new.md > work/prompt.md
Fails if a placeholder has no value, so a prompt never goes out half-filled.
"""
import os
import re
import sys

tpl = open(sys.argv[1], encoding="utf-8").read()
missing = sorted({m for m in re.findall(r"\{\{([A-Z0-9_]+)\}\}", tpl) if not os.environ.get(m)})
if missing:
    sys.exit(f"render_prompt: no value for {', '.join(missing)}")
sys.stdout.write(re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: os.environ[m.group(1)], tpl))
