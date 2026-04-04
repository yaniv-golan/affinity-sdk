# Makefile for xaffinity MCP server distribution
#
# Build targets:
#   mcpb    - MCPB bundle for one-click installation (Claude Desktop, etc.)
#   plugin  - Claude Code plugin ZIP (for marketplace distribution)
#
# The MCPB bundle is the preferred distribution format for MCP clients.
# The plugin ZIP is retained for Claude Code marketplace compatibility.

PLUGIN_DIR := .claude-plugin
ZIP_FILE := $(PLUGIN_DIR)/xaffinity-mcp-plugin.zip
VERSION_FILE := VERSION
MCPB_OUTPUT_DIR := dist

# MCP server files to include in plugin ZIP
MCP_FILES := \
	xaffinity-mcp.sh \
	xaffinity-mcp-env.sh \
	VERSION \
	COMPATIBILITY

# MCP server directories to include in plugin ZIP
MCP_DIRS := \
	completions \
	lib \
	prompts \
	providers \
	resources \
	scripts \
	server.d \
	tools

.PHONY: mcpb mcpb-validate plugin clean verify test-plugin help all

# Default target: build both MCPB bundle and plugin ZIP
all: mcpb plugin

# Build MCPB bundle using mcp-bash framework (requires mcp-bash 0.9.1+)
mcpb:
	@echo "Building MCPB bundle..."
	@mkdir -p "$(MCPB_OUTPUT_DIR)"
	@mcp-bash bundle --output "$(MCPB_OUTPUT_DIR)" --verbose
	@echo ""
	@echo "MCPB bundle created in $(MCPB_OUTPUT_DIR)/"
	@ls -la "$(MCPB_OUTPUT_DIR)"/*.mcpb 2>/dev/null || true

# Validate MCPB bundle without creating
mcpb-validate:
	@echo "Validating MCPB bundle configuration..."
	@mcp-bash bundle --validate

# Build Claude Code plugin ZIP (legacy distribution)
plugin: clean-plugin
	@echo "Building plugin ZIP..."
	@rm -f "$(ZIP_FILE)"
	@zip -q "$(ZIP_FILE)" $(MCP_FILES)
	@for dir in $(MCP_DIRS); do \
		if [ -d "$$dir" ]; then \
			zip -qr "$(ZIP_FILE)" "$$dir"; \
		fi; \
	done
	@# Copy VERSION to plugin dir for version comparison at runtime
	@cp "$(VERSION_FILE)" "$(PLUGIN_DIR)/VERSION"
	@echo "Plugin built: $(ZIP_FILE)"
	@echo "Version: $$(cat $(VERSION_FILE))"
	@echo "Size: $$(du -h "$(ZIP_FILE)" | cut -f1)"

# Verify the plugin ZIP contents
verify:
	@echo "Verifying plugin ZIP..."
	@test -f "$(ZIP_FILE)" || (echo "Error: ZIP not found" && exit 1)
	@test -f "$(PLUGIN_DIR)/VERSION" || (echo "Error: VERSION not found" && exit 1)
	@echo "Bundle version: $$(cat $(PLUGIN_DIR)/VERSION)"
	@unzip -l "$(ZIP_FILE)" | head -20
	@echo "..."
	@echo "Total files: $$(unzip -l "$(ZIP_FILE)" | tail -1)"

# Test the plugin extraction and MCP server
test-plugin: plugin
	@echo "Testing plugin extraction..."
	@cd $(PLUGIN_DIR) && rm -rf .mcp-extracted
	@cd $(PLUGIN_DIR) && ./xaffinity-mcp.sh --help >/dev/null 2>&1 || true
	@test -x $(PLUGIN_DIR)/.mcp-extracted/xaffinity-mcp.sh || \
		(echo "FAIL: Extraction did not create executable" && exit 1)
	@test -f $(PLUGIN_DIR)/.mcp-extracted/VERSION || \
		(echo "FAIL: VERSION not extracted" && exit 1)
	@echo "PASS: Plugin extraction test"

# Clean all build artifacts
clean: clean-plugin clean-mcpb

# Clean plugin artifacts only
clean-plugin:
	@echo "Cleaning plugin artifacts..."
	@rm -f "$(ZIP_FILE)"
	@rm -f "$(PLUGIN_DIR)/VERSION"
	@rm -rf "$(PLUGIN_DIR)/.mcp-extracted"

# Clean MCPB artifacts only
clean-mcpb:
	@echo "Cleaning MCPB artifacts..."
	@rm -rf "$(MCPB_OUTPUT_DIR)"

help:
	@echo "Available targets:"
	@echo ""
	@echo "  MCPB (preferred for MCP clients):"
	@echo "    mcpb          - Build MCPB bundle (requires mcp-bash 0.9.1+)"
	@echo "    mcpb-validate - Validate MCPB configuration"
	@echo ""
	@echo "  Claude Code Plugin:"
	@echo "    plugin        - Build plugin ZIP archive"
	@echo "    verify        - Verify ZIP contents"
	@echo "    test-plugin   - Build and test extraction"
	@echo ""
	@echo "  General:"
	@echo "    all           - Build both MCPB and plugin"
	@echo "    clean         - Remove all build artifacts"
	@echo "    help          - Show this help message"
