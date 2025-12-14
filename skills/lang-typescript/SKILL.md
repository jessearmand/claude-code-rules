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
5. Lint the code

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
