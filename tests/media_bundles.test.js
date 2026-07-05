import assert from "node:assert/strict";
import test from "node:test";

import { dumpFrontMatter, parseFrontMatter } from "../scripts/media_bundles.js";

test("parseFrontMatter parses LF YAML front matter", () => {
  const [data, body] = parseFrontMatter("---\ntitle: LF\ntags:\n  - test\n---\nBody\n");

  assert.deepEqual(data, { title: "LF", tags: ["test"] });
  assert.equal(body, "Body\n");
});

test("parseFrontMatter parses CRLF YAML front matter", () => {
  const [data, body] = parseFrontMatter(
    "---\r\ntitle: CRLF\r\nold_url: https://example.test/image.jpg\r\n---\r\nBody\r\n",
  );

  assert.deepEqual(data, { title: "CRLF", old_url: "https://example.test/image.jpg" });
  assert.equal(body, "Body\r\n");
});

test("parseFrontMatter leaves text without front matter unchanged", () => {
  const text = "# Heading\n\nBody\n";
  const [data, body] = parseFrontMatter(text);

  assert.deepEqual(data, {});
  assert.equal(body, text);
});

test("parseFrontMatter ignores delimiter blocks after document start", () => {
  const text = "# Heading\n\n---\ntitle: Not front matter\n---\nBody\n";
  const [data, body] = parseFrontMatter(text);

  assert.deepEqual(data, {});
  assert.equal(body, text);
});

test("dumpFrontMatter trims CRLF leading blank lines from body", () => {
  assert.equal(
    dumpFrontMatter({ title: "Body" }, "\r\n\r\nBody\n"),
    "---\ntitle: Body\n---\n\nBody\n",
  );
});
