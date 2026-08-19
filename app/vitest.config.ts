import { defineConfig } from "vitest/config";

/*
 * Node, not a DOM. The tests here are about the IPC contract and the state
 * rules — which command name is sent, what an event does to the model, whether
 * a control may be enabled — and none of that needs a rendered tree.
 *
 * That is a deliberate line rather than a shortcut. A test that mounted the
 * component tree to assert "the button is disabled" would be asserting the rule
 * through two layers of rendering, and would keep passing if the rule moved into
 * a component and quietly diverged from the one in `model.ts`. The rules live in
 * one module precisely so they can be checked as functions.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
