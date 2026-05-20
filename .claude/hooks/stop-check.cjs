#!/usr/bin/env node
require(resolve('stop-check.cjs'));
function resolve(name) {
  const fs = require('fs');
  const paths = [`C:\\laragon\\www\\CarRental\\.claude\\hooks\\${name}`, `/mnt/c/laragon/www/CarRental/.claude/hooks/${name}`];
  const hit = paths.find((file) => fs.existsSync(file));
  if (!hit) throw new Error(`Missing shared Vrooem hook: ${name}`);
  return hit;
}
