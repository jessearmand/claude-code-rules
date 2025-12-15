# Python ast-grep Patterns

## Functions with Type Hints

```bash
ast-grep scan --inline-rules 'id: typed-functions
language: python
rule:
  kind: function_definition
  has:
    kind: type
    stopBy: end' /path
```

## Functions with Error Handling

```bash
ast-grep scan --inline-rules 'id: error-handling
language: python
rule:
  kind: function_definition
  has:
    kind: try_statement
    stopBy: end' /path
```

## Print with Multiple Arguments

```bash
ast-grep run --pattern 'print($ARG1, $ARG2, $$$REST)' --lang python /path
```

## File Operations

```bash
ast-grep run --pattern 'with open($$$ARGS) as $F: $$$BODY' --lang python /path
```

## Python AST Node Kinds

| Kind | Description |
|------|-------------|
| `function_definition` | `def foo():` |
| `class_definition` | `class Foo:` |
| `try_statement` | `try: ... except:` |
| `with_statement` | `with ... as ...:` |
| `type` | Type annotations |
| `call` | Function calls |
