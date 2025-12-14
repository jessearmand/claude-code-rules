# Testing with Vitest

Patterns and conventions for Vitest testing.

## Framework

All tests use Vitest: `describe`, `it`, `expect`, `vi`

## File Organization

- Logic tests: `*.test.ts`
- Component tests: `*.test.tsx`
- Co-locate tests with source files
- Configuration in `vitest.config.ts`

## Setup/Teardown

```typescript
beforeEach(() => {
    vi.resetAllMocks();
});

afterEach(() => {
    vi.restoreAllMocks();
});
```

## Mocking

### ES Modules

```typescript
vi.mock('module-name', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        specificFunction: vi.fn()
    };
});
```

### Mocking Order

For critical dependencies that affect module-level constants, place `vi.mock` at the **very top** before other imports:

```typescript
// vi.mock calls must be first
vi.mock('os', async (importOriginal) => {
    const actual = await importOriginal();
    return { ...actual, homedir: vi.fn() };
});

// Then other imports
import { myFunction } from './myModule';
```

### Hoisting

Define mocks before use in factory:

```typescript
const myMock = vi.hoisted(() => vi.fn());

vi.mock('./module', () => ({
    myFunction: myMock
}));
```

### Mock Functions

```typescript
const mock = vi.fn();

// Define behavior
mock.mockImplementation(() => 'result');
mock.mockResolvedValue('async result');
mock.mockRejectedValue(new Error('fail'));
```

### Spying

```typescript
const spy = vi.spyOn(object, 'methodName');

afterEach(() => {
    spy.mockRestore();
});
```

## Commonly Mocked Modules

- **Node.js**: `fs`, `fs/promises`, `os`, `path`, `child_process`
- **External SDKs**: `@google/genai`, `@modelcontextprotocol/sdk`
- **Internal modules**: Dependencies from other packages

## React Component Testing (Ink)

```typescript
import { render } from 'ink-testing-library';

it('renders correctly', () => {
    const { lastFrame } = render(
        <Context.Provider value={mockValue}>
            <MyComponent />
        </Context.Provider>
    );

    expect(lastFrame()).toContain('Expected text');
});
```

## Async Testing

```typescript
it('handles async operations', async () => {
    await expect(promise).resolves.toBe('value');
    await expect(badPromise).rejects.toThrow('error');
});
```

### Fake Timers

```typescript
vi.useFakeTimers();

await vi.advanceTimersByTimeAsync(1000);
await vi.runAllTimersAsync();
```

## Best Practices

- Examine existing tests before adding new ones
- Pay attention to mocks at top of test files
- Test public APIs, not internals
- If you need to spy on unexported functions, consider extracting them to a separate module
