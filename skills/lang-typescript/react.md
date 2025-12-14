# React Best Practices

Write efficient, optimizable React code with React Compiler in mind.

## Core Guidelines

### Use Functional Components with Hooks

- No class components
- Manage state with `useState` or `useReducer`
- Side effects in `useEffect` or event handlers

### Keep Components Pure

```typescript
// DO: Pure render function
function UserCard({ user }: { user: User }) {
    return <div>{user.name}</div>;
}

// DON'T: Side effects in render
function UserCard({ user }: { user: User }) {
    fetch('/api/log', { method: 'POST' }); // Bad!
    return <div>{user.name}</div>;
}
```

### One-Way Data Flow

- Pass data down through props
- Lift state to common parent when sharing
- Use Context for app-wide concerns
- No global mutations

### Immutable State Updates

```typescript
// DO: Create new object
setUser({ ...user, name: 'New Name' });
setItems([...items, newItem]);

// DON'T: Mutate directly
user.name = 'New Name'; // Bad!
items.push(newItem); // Bad!
```

## useEffect Guidelines

**Think hard before using useEffect.** It's primarily for synchronization with external state.

### DON'T:

```typescript
// Bad: Setting state in useEffect
useEffect(() => {
    setDerivedValue(computeFromProps(props));
}, [props]);
```

### DO:

```typescript
// Good: Compute during render
const derivedValue = computeFromProps(props);

// Good: Event handler for user actions
const handleSubmit = () => {
    submitForm(formData);
};
```

### When useEffect is Appropriate:

- Subscribing to external data sources
- Setting up WebSocket connections
- DOM measurements
- Third-party library integration

### Always:

- Include all dependencies
- Return cleanup functions
- Don't suppress ESLint rules

## Rules of Hooks

- Call hooks at the top level only
- Call hooks from React functions only
- No hooks in loops, conditions, or nested functions

## Refs

Use `useRef` sparingly:

**Appropriate uses:**
- Focusing inputs
- Managing animations
- Third-party library integration

**Avoid:**
- Storing reactive state
- Reading/writing during render

## React Compiler

With React Compiler enabled:

- Skip `useMemo`, `useCallback`, `React.memo`
- Write clear, simple components
- Let the compiler optimize
- Focus on correct data flow

## Composition

- Small, focused components
- Compose rather than inherit
- Extract custom hooks for reusable logic

## Concurrent Mode

Write code that works if rendered multiple times:

```typescript
// DO: Functional state updates
setCount(c => c + 1);

// DON'T: Race condition risk
setCount(count + 1);
```

## Data Fetching

- Parallel fetching when possible
- Use Suspense for loading states
- Co-locate data requirements with components
- Cache and dedupe requests

## User Experience

- Skeleton screens over spinners
- Graceful error handling with boundaries
- Render partial data as available
- Declarative loading states with Suspense
