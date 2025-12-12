# Swift ast-grep Patterns

## All Functions

```bash
ast-grep scan --inline-rules 'id: swift-functions
language: swift
rule:
  kind: function_declaration' /path
```

## Throwing Functions

```bash
ast-grep scan --inline-rules 'id: swift-throws
language: swift
rule:
  kind: function_declaration
  has:
    kind: throws
    stopBy: end' /path
```

## Try Await Expressions

```bash
ast-grep run --pattern 'try await $EXPR' --lang swift /path
```

## Guard Statements

```bash
ast-grep run --pattern 'guard $CONDITION else { $$$BODY }' --lang swift /path
```

## Async Functions

```bash
ast-grep scan --inline-rules 'id: swift-async
language: swift
rule:
  kind: function_declaration
  has:
    pattern: async
    stopBy: end' /path
```

## Swift AST Node Kinds

| Kind | Description |
|------|-------------|
| `function_declaration` | `func foo() {}` |
| `class_declaration` | `class Foo {}` |
| `struct_declaration` | `struct Foo {}` |
| `protocol_declaration` | `protocol Foo {}` |
| `throws` | Throwing function marker |
| `try_expression` | `try expression` |
