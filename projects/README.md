# Product Offerings

`projects/` holds independently shaped product offerings alongside the
`ai-bootstrap` harness. It is not a sixth root pillar, a shared-skill source, a
tool facade, or an installation surface. The parent repository shape governs
the surrounding harness; each offering's local `about/` and child OpenSpec
change govern product behavior.

## Adding an Offering

Each immediate `projects/<offering>/` directory must be listed in this file and expose
its entrypoint at `<offering>/about/README.md`. Run that offering's OpenSpec
commands from its own root; do not add product-local material to the shared
skill mirror or installation flow.
