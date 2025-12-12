# TypeScript ast-grep Patterns

## All Functions

```bash
ast-grep scan --inline-rules 'id: ts-functions
language: typescript
rule:
  kind: function_declaration' /path
```

## Async Functions

```bash
ast-grep run --pattern 'async function $NAME($$$PARAMS) { $$$BODY }' --lang typescript /path
```

## Arrow Functions

```bash
ast-grep run --pattern 'const $NAME = ($$$PARAMS) => $BODY' --lang typescript /path
```

## Console.log Calls

```bash
ast-grep run --pattern 'console.log($$$ARGS)' --lang typescript /path
```

## Await Expressions

```bash
ast-grep run --pattern 'await $EXPR' --lang typescript /path
```

## TypeScript AST Node Kinds

| Kind | Description |
|------|-------------|
| `function_declaration` | `function foo() {}` |
| `class_declaration` | `class Foo {}` |
| `arrow_function` | `() => {}` |
| `try_statement` | `try {} catch {}` |
| `await_expression` | `await promise` |
| `call_expression` | `foo()` |
