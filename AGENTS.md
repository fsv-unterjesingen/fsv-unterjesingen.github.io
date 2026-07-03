# AGENTS.md

## Background info

This is the website of the FSV Unterjesingen (`fsv-unterjesingen.de`), a German gliding club. The website uses Hugo, and was migrated from a WordPress site.

### Scripts

- `scripts/import_wordpress_media.py`: Migration script that parses a WordPress SQL dump and tar backup, extracts original image/video attachments into Hugo assets, and writes the preserved media library JSON plus an import manifest. No longer needed now that the migration has taken place.
- `scripts/media_bundles.py`: Shared helper module for media-bundle scripts, covering front matter normalization, file inspection, bundle CRUD, and duplicate detection.
- `scripts/media_bundles.js`: Shared helper module for the Node-based media editor, covering front matter normalization, bundle CRUD, duplicate detection, and runtime media inspection.
- `scripts/media_editor.js`: A local media editor that runs as an HTTP server and allows for browsing, uploading, and deleting of media, as well as media metadata editing. This functionality is similar to what the WordPress Media Library would have provided on the old site.
- `scripts/migrate_legacy_blog.py`: Script to crawl the legacy blog, convert posts into Hugo bundles under `content/blog`, download referenced images, and record a migration manifest and warnings. No longer needed now that the blog has been migrated.
- `scripts/migrate_wordpress_media_to_bundles.py`: Converts the imported WordPress media library into one-file Hugo leaf bundles under `content/media`, copying originals and generating normalized front matter. No longer needed now that the media has been migrated from the old site.
- `scripts/normalize_media_front_matter.py`: Rewrites media bundle front matter into canonical form by removing redundant derived fields, renaming legacy keys, and trimming removable image metadata. No longer needed now that the media has been migrated from the old site.
- `scripts/prune_redundant_image_meta.py`: Removes `image_meta` from media bundles when it is empty, placeholder-only, or fully recoverable from the original image metadata.
- `scripts/sync_wordpress_upload_symlinks.py`: Creates or updates `static/wp-content/uploads/...` symlinks so legacy WordPress upload URLs resolve to the current media bundle files. Useful for maintenance of the migrated media library; the Node media editor removes matching legacy upload symlinks itself when an image is deleted.
- `scripts/update-tile-cache.py`: Downloads map style assets and a bounded set of OpenFreeMap/OpenAIP vector tiles into `static/tiles`, then rebuilds the tile-cache manifest. This script is executed monthly as part of the Continuous Integration pipeline.


## Working agreements

- You may only execute `git` commands to enquire about the status of the repository, to get diffs, or to retrieve any other relevant information (but you may not execute `git config`). Under no circumstances are you allowed to execute any `git` commands that are effectful, such as checking out a commit, making a commit, or rewriting history.
- When you create test builds with `hugo`, you must always build into `/tmp`. Under no circumstances can the current directory be used for that. If a hugo test build takes a long time, chances are I have my own hugo server running, and the two processes are interfering. In that case, let me know things are taking a long time, and I will stop my server. When you are done with the test build, always clean up any artifacts you have left in `/tmp` so that that directory doesn't fill up.
