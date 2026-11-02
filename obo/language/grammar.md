# Grammar

Part of the obo language reference. Index and core syntax:
[LANGUAGE.md](../LANGUAGE.md).

This is an informative EBNF of the language the compiler accepts. The parser is
single-pass and the chapters define the contextual rules: type checking, name
resolution, ownership, effect summaries and the exact diagnostics. `;` may end a
statement wherever a newline does.

```ebnf
program     = "module" IDENT { import } { topdecl } EOF ;
import      = "import" STRING [ "as" IDENT ] ;

topdecl     = [ "pub" ] ( typedecl | constdecl | vardecl | fndecl )
            | "impl" type "for" type
            | "native" "fn" IDENT "(" [ params ] ")" [ ":" type ] ;

typedecl    = "type" IDENT [ "=" typespec ] ;
typespec    = "ref" "record" "{" { field } "}"
            | "closed" "{" { variant } "}"
            | "foreign" STRING
            | "interface" "{" { member } "}"
            | type ;
variant     = IDENT "{" { field } "}" ;
field       = [ "pub" ] IDENT { "," IDENT } ":" type ";" ;

constdecl   = "const" IDENT "=" expr ";" ;
vardecl     = ( "var" | "let" ) IDENT [ ":" refType ] [ "=" expr ] ";" ;
fndecl      = "fn" [ "(" receiver ")" ] IDENT "(" [ params ] ")"
              [ ":" type ] [ block ] ;
receiver    = [ "mut" ] IDENT ":" refType ;
params      = param { "," param } ;
param       = [ "mut" ] IDENT ":" refType ;
member      = [ "pure" | "impure" ] [ "mut" ] "fn" IDENT
              "(" [ params ] ")" [ ":" type ] ";" ;

refType     = [ "&" ] type ;
type        = "?" type
            | "^" type
            | "[]" type
            | "Result" "[" type "]"
            | "fn" "(" [ types ] ")" [ ":" type ]
            | IDENT
            | "int" | "byte" | "bool" | "float" | "decimal" | "string" ;
types       = type { "," type } ;

block       = "{" { statement } "}" ;
statement   = vardecl
            | "if" expr block [ "else" ( "if" expr block [ "else" block ] | block ) ]
            | "while" expr block
            | "for" IDENT "in" expr [ ".." expr ] block
            | match
            | "return" [ expr ] ";"
            | "break" ";"
            | "continue" ";"
            | "defer" ( block | statement )
            | block
            | designator [ assignop expr ]
            | "try" expr ;
match       = "match" expr "{" { arm } [ "else" block ] "}" ;
arm         = ( IDENT | expr { "," expr } ) block ;
assignop    = "=" | "+=" | "-=" | "*=" | "%=" ;

expr        = conjunction { "||" conjunction } ;
conjunction = relation { "&&" relation } ;
relation    = sum [ ( "<" | "<=" | ">" | ">=" | "==" | "!=" ) sum ] ;
sum         = product { ( "+" | "-" | "|" | "^" ) product } ;
product     = unary { ( "*" | "/" | "%" | "<<" | ">>" | "&" ) unary } ;
unary       = ( "!" | "-" | "~" | "&" | "try" ) unary | postfix ;
postfix     = primary { "." IDENT | "[" expr "]" | "(" [ args ] ")" } ;
args        = expr { "," expr } ;
primary     = INT | FLOAT | DECIMAL | STRING | CHAR | "true" | "false" | "nil"
            | designator
            | "(" expr ")"
            | "new" ( "[]" type "(" expr ")" | IDENT "{" [ fields ] "}" )
            | "ok" "(" expr ")" | "err" "(" expr "," expr ")"
            | "fn" [ "[" IDENT { "," IDENT } "]" ] "(" [ params ] ")"
              [ ":" type ] block ;
designator  = IDENT { "." IDENT | "[" expr "]" } ;
fields      = IDENT ":" expr { "," IDENT ":" expr } ;
```

Notes:

- A `match` arm is a variant name for a closed type, a constant expression list
  for `int`/`byte`, or `ok`/`err` for a `Result`. `else` is last.
- `&` is a borrow only in a unary position or a `refType`; elsewhere it is
  bitwise and.
- `fn` at the top level declares a function; at expression position it is a
  lambda. A top-level `fn` with no block is a forward heading.
- `pub` is allowed on top-level declarations and on record fields, not on
  variants or locals.
- `try` is an expression with its own placement rules; see
  [results.md](results.md).
- `native fn` is the cinterop declaration form; it adds `out name: Type from
  input` parameters and has no body. It is described in the cinterop package,
  not in the core language chapters.
- `final` is not a keyword. There are no user-defined operators, generics or
  macros.
