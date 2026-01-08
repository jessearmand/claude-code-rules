---
name: lang-typescript
description: TypeScript/JavaScript development with Vitest testing, React patterns, and functional programming best practices. Use when writing or reviewing TypeScript/JavaScript code.
---

# TypeScript/JavaScript Development

Write functional, type-safe TypeScript/JavaScript code.

## Validation Workflow

Before submitting changes:

1. Review `package.json` for available scripts
2. Build the repository
3. Run all tests
4. Check for type errors
5. Lint with Biome: `bunx --bun biome check` (or `npx @biomejs/biome check`)

## Linting with Biome

Biome is the recommended linter for TypeScript/JavaScript projects. It provides 419+ lint rules, formatting, and assist actions in a single fast tool.

### Quick Commands

```bash
# Check for lint errors and formatting issues
bunx --bun biome check ./src

# Fix safe issues automatically
bunx --bun biome check --write ./src

# Fix all issues including unsafe fixes (review changes)
bunx --bun biome check --write --unsafe ./src

# Lint only (no formatting)
bunx --bun biome lint ./src

# Format only
bunx --bun biome format --write ./src
```

### Configuration

Create `biome.json` in project root:

```json
{
    "$schema": "https://biomejs.dev/schemas/2.0.5/schema.json",
    "linter": {
        "enabled": true,
        "rules": {
            "recommended": true,
            "suspicious": {
                "noExplicitAny": "error"
            }
        }
    },
    "formatter": {
        "enabled": true,
        "indentStyle": "space",
        "indentWidth": 4
    },
    "assist": {
        "enabled": true,
        "actions": {
            "source": {
                "organizeImports": "on"
            }
        }
    },
    "javascript": {
        "formatter": {
            "quoteStyle": "single",
            "semicolons": "always"
        }
    }
}
```

### Rule Groups

- **correctness**: Catches guaranteed bugs (enabled by default)
- **suspicious**: Detects likely bugs or useless code
- **style**: Enforces consistent code style (warnings by default)
- **complexity**: Identifies overly complex code
- **security**: Detects potential security flaws
- **a11y**: Accessibility rules for React/HTML

### Assist Actions

Biome Assist provides safe code transformations that improve code quality. Unlike lint rules, assist actions always offer a code fix.

Available actions in the `source` group:
- **organizeImports**: Sort and group import statements
- **useSortedKeys**: Sort object keys alphabetically (useful for JSON/config files)

```bash
# Run assist actions only
bunx --bun biome check --formatter-enabled=false --linter-enabled=false

# Run all checks including assist (default behavior)
bunx --bun biome check --write ./src
```

### Suppressing Rules

```typescript
// Suppress specific rule for next line
// biome-ignore lint/suspicious/noExplicitAny: external API requires any
const data: any = externalApi.getData();

// Suppress formatting
// biome-ignore format: matrix alignment
const matrix = [
    [1, 0, 0],
    [0, 1, 0],
];
```

### Editor Integration

VS Code settings for Biome:

```json
{
    "editor.defaultFormatter": "biomejs.biome",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.biome": "explicit",
        "source.organizeImports.biome": "explicit",
        "source.action.useSortedKeys.biome": "explicit"
    }
}
```

Code action codes for assist:
- `source.fixAll.biome` - Apply all safe lint fixes
- `source.organizeImports.biome` - Sort and organize imports
- `source.action.useSortedKeys.biome` - Sort object keys

### Migrating from ESLint

```bash
# Migrate ESLint config to Biome
bunx --bun biome migrate eslint

# Suppress existing violations during migration
bunx --bun biome lint --write --unsafe --suppress="suppressed due to migration"
```

## Core Principles

### Prefer Plain Objects over Classes

- Use TypeScript interfaces/types with plain objects
- Classes add complexity that doesn't fit React's model
- Plain objects are easier to serialize, test, and reason about

```typescript
// Prefer this
interface User {
    id: string;
    name: string;
}
const user: User = { id: '1', name: 'Alice' };

// Avoid this
class User {
    constructor(public id: string, public name: string) {}
}
```

### ES Module Encapsulation

Use `import`/`export` for public API definition instead of class members:

- Exported = public API
- Not exported = private to module
- Test public APIs, not internals

### Type Safety

**Avoid `any`**:
- Loses type safety
- Masks underlying issues
- Reduces readability

**Prefer `unknown` over `any`**:

```typescript
function processValue(value: unknown) {
    if (typeof value === 'string') {
        console.log(value.toUpperCase()); // Type narrowed
    }
}
```

**Use type assertions sparingly**:
- `as Type` bypasses compiler checks
- Only use with external libraries or when you have more info than compiler

### Functional Array Operations

Prefer array methods over loops:

```typescript
// Prefer
const doubled = items.map(x => x * 2);
const evens = items.filter(x => x % 2 === 0);
const sum = items.reduce((acc, x) => acc + x, 0);

// Avoid imperative loops for transformations
```

## Comments Policy

Only write high-value comments. Avoid talking to the user through comments.

## Detailed Guides

- [Testing](testing.md) - Vitest patterns and mocking conventions
- [React](react.md) - React best practices with React Compiler focus
