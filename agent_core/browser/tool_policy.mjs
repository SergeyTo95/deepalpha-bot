export const BROWSER_TOOL_ALLOWLIST = Object.freeze([
  'mcp__playwright-mcp__browser_navigate',
  'mcp__playwright-mcp__browser_snapshot',
  'mcp__playwright-mcp__browser_click',
  'mcp__playwright-mcp__browser_type',
  'mcp__playwright-mcp__browser_fill_form',
  'mcp__playwright-mcp__browser_select_option',
  'mcp__playwright-mcp__browser_press_key',
  'mcp__playwright-mcp__browser_wait_for',
  'mcp__playwright-mcp__browser_tabs',
  'mcp__playwright-mcp__browser_navigate_back',
]);

export function isAllowedBrowserTool(name) {
  return BROWSER_TOOL_ALLOWLIST.includes(name);
}
