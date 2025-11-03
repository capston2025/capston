Use the auto-fix-loop agent to continuously test and fix GAIA until all tests pass.

Test the site https://final-blog-25638597.figma.site using test plan gaia/artifacts/plans/realistic_test_no_selectors.json

Run up to 10 iterations maximum.

Process:
1. Check if MCP host is running on port 8001 (start if needed)
2. Run integration tests
3. Analyze any failures
4. Automatically fix code issues
5. Repeat until all tests pass or max iterations reached

Report progress after each iteration.
