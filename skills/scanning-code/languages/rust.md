# Rust ast-grep Patterns

## All Functions

```bash
ast-grep scan --inline-rules 'id: rust-functions
language: rust
rule:
  kind: function_item' /path
```

## Unwrap Calls (potential panics)

```bash
ast-grep run --pattern '$EXPR.unwrap()' --lang rust /path
```

## Expect Calls (potential panics with message)

```bash
ast-grep run --pattern '$EXPR.expect($MSG)' --lang rust /path
```

## Functions Returning Result

```bash
ast-grep scan --inline-rules 'id: result-functions
language: rust
rule:
  kind: function_item
  has:
    pattern: -> Result<$T, $E>
    stopBy: end' /path
```

## Rust AST Node Kinds

| Kind | Description |
|------|-------------|
| `function_item` | `fn foo() {}` |
| `impl_item` | `impl Foo {}` |
| `struct_item` | `struct Foo {}` |
| `enum_item` | `enum Foo {}` |
| `trait_item` | `trait Foo {}` |
| `macro_invocation` | `println!()` |
