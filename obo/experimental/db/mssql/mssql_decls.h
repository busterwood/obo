/* Minimal vendored declarations for the Microsoft SQL Server ODBC surface used
 * by the experimental db/mssql package on cinterop v2.
 *
 * Provenance: independently written for this project from the public ODBC 3.x
 * API (unixODBC / Microsoft ODBC Driver 18 for SQL Server) and from the
 * measured ABI on x86_64-linux-gnu. The host has the unixODBC headers at
 * /usr/include/sql*.h, but the committed build must not depend on them, so the
 * declarations used by this binding are reproduced here:
 *
 *   sizeof(SQLWCHAR)=2  sizeof(SQLSMALLINT)=2  sizeof(SQLINTEGER)=4
 *   sizeof(SQLLEN)=8    sizeof(SQLULEN)=8      sizeof(SQLHANDLE)=8
 *
 * The Phase 1 probe (see README.md) measured these values against the live
 * driver. This file therefore does NOT claim a header check: mssql.cbind records
 * signature mode "declarations-only", and the live tests are the ABI gate.
 *
 * Narrow (ANSI) entry points are NOT used for text metadata or SQL text: the
 * driver converts those through the process locale, so UTF-8 input and output
 * would be wrong outside a UTF-8 locale. The W entry points are used for the
 * connection string, SQL text, column names, and diagnostics. Result values and
 * parameter values are carried as UTF-8 through SQL_C_CHAR, which the driver
 * converts correctly (measured); see README.md.
 *
 * Copyright and licence: these declarations are an ABI description using the
 * public ODBC type and function names. ODBC itself is not reproduced.
 */
#ifndef OBO_DB_MSSQL_DECLS_H
#define OBO_DB_MSSQL_DECLS_H

/* Fixed-width ODBC types on x86_64-linux-gnu (measured; see README.md). */
typedef signed short   SQLSMALLINT;
typedef unsigned short SQLUSMALLINT;
typedef int            SQLINTEGER;
typedef unsigned int   SQLUINTEGER;
typedef long           SQLLEN;
typedef unsigned long  SQLULEN;
typedef unsigned short SQLWCHAR;
typedef void          *SQLPOINTER;
typedef void          *SQLHANDLE;
typedef void          *SQLHENV;
typedef void          *SQLHDBC;
typedef void          *SQLHSTMT;
typedef void          *SQLHWND;
typedef signed short   SQLRETURN;

/* The binding's foreign handle types. The value of an ms_conn * is a SQLHDBC and
 * the value of an ms_stmt * is a SQLHSTMT; both are pointer-sized opaque words.
 * Declaring project types keeps the cinterop borrow/own shapes honest while the
 * ABI stays identical to the driver's void* handles. */
typedef struct ms_conn ms_conn;
typedef struct ms_stmt ms_stmt;

/* Handle types. */
#define SQL_HANDLE_ENV   1
#define SQL_HANDLE_DBC   2
#define SQL_HANDLE_STMT  3

/* Return codes used by the wrapper (the raw SQLRETURN is exposed as status). */
#define SQL_SUCCESS             0
#define SQL_SUCCESS_WITH_INFO   1
#define SQL_NO_DATA             100
#define SQL_ERROR               (-1)
#define SQL_INVALID_HANDLE      (-2)
#define SQL_STILL_EXECUTING     2
#define SQL_NEED_DATA           99

/* SQLRETURN tests (the official ODBC macros; the low bit is the info flag). */
#define SQL_SUCCEEDED(rc) (((rc) & ~1) == 0)
#define SQL_FAILED(rc)    (((rc) & ~1) != 0)

/* Length and indicator sentinels. */
#define SQL_NTS        (-3)
#define SQL_NULL_DATA  (-1)
#define SQL_NO_TOTAL   (-4)
#define SQL_NULL_HANDLE 0
#define SQL_NULL_HENV   0
#define SQL_NULL_HDBC   0
#define SQL_NULL_HSTMT  0

/* Environment and connection attributes. */
#define SQL_ATTR_ODBC_VERSION 200
#define SQL_OV_ODBC3          3
#define SQL_DRIVER_NOPROMPT   0

/* SQLFreeStmt options. */
#define SQL_CLOSE        0
#define SQL_RESET_PARAMS 3

/* SQLDescribeCol nullable values. */
#define SQL_NO_NULLS          0
#define SQL_NULLABLE          1
#define SQL_NULLABLE_UNKNOWN  2

/* SQL data type codes (SQLDescribeCol DataType). */
#define SQL_CHAR           1
#define SQL_NUMERIC        2
#define SQL_DECIMAL        3
#define SQL_INTEGER        4
#define SQL_SMALLINT       5
#define SQL_VARCHAR        12
#define SQL_TYPE_DATE      91
#define SQL_TYPE_TIMESTAMP 93

/* SQL data type codes above 0x7fff are negative. */
#define SQL_LONGVARCHAR    (-1)
#define SQL_BINARY         (-2)
#define SQL_VARBINARY      (-3)
#define SQL_LONGVARBINARY  (-4)
#define SQL_BIGINT         (-5)
#define SQL_TINYINT        (-6)
#define SQL_BIT            (-7)
#define SQL_WCHAR          (-8)
#define SQL_WVARCHAR       (-9)
#define SQL_WLONGVARCHAR   (-10)
#define SQL_GUID           (-11)
/* Microsoft SQL Server driver-specific type codes (SQLDescribeCol). */
#define SQL_XML            (-152)
#define SQL_SS_TIME2       (-154)
#define SQL_SS_TIMESTAMPOFFSET (-155)

/* C data type codes. */
#define SQL_C_CHAR    SQL_CHAR
#define SQL_C_WCHAR   SQL_WCHAR
#define SQL_C_BINARY  SQL_BINARY
#define SQL_C_SBIGINT (SQL_BIGINT + (-20))

/* SQLBindParameter InputOutputType. */
#define SQL_PARAM_INPUT 1

/* Hard limits this binding enforces in C as well as in obo. */
#define MS_MAX_SQL      1048576
#define MS_MAX_VALUE    16777216
#define MS_MAX_COLUMNS  4096
#define MS_MAX_PARAMS   32

/* ------------------------------------------------------------- ODBC functions
 * The exact prototypes used by the shims. W entry points take SQLWCHAR*; the
 * shims pass NUL-terminated UTF-16 buffers converted from UTF-8. */
extern SQLRETURN SQLAllocHandle(SQLSMALLINT HandleType, SQLHANDLE InputHandle,
                                SQLHANDLE *OutputHandle);
extern SQLRETURN SQLFreeHandle(SQLSMALLINT HandleType, SQLHANDLE Handle);
extern SQLRETURN SQLSetEnvAttr(SQLHENV EnvironmentHandle, SQLINTEGER Attribute,
                               SQLPOINTER Value, SQLINTEGER StringLength);
extern SQLRETURN SQLDriverConnectW(SQLHDBC ConnectionHandle, SQLHWND WindowHandle,
                                   SQLWCHAR *InConnectionString,
                                   SQLSMALLINT StringLength1,
                                   SQLWCHAR *OutConnectionString,
                                   SQLSMALLINT BufferLength,
                                   SQLSMALLINT *StringLength2Ptr,
                                   SQLUSMALLINT DriverCompletion);
extern SQLRETURN SQLDisconnect(SQLHDBC ConnectionHandle);
extern SQLRETURN SQLExecDirectW(SQLHSTMT StatementHandle, SQLWCHAR *StatementText,
                                SQLINTEGER TextLength);
extern SQLRETURN SQLPrepareW(SQLHSTMT StatementHandle, SQLWCHAR *StatementText,
                             SQLINTEGER TextLength);
extern SQLRETURN SQLExecute(SQLHSTMT StatementHandle);
extern SQLRETURN SQLFetch(SQLHSTMT StatementHandle);
extern SQLRETURN SQLNumResultCols(SQLHSTMT StatementHandle,
                                  SQLSMALLINT *ColumnCountPtr);
extern SQLRETURN SQLNumParams(SQLHSTMT StatementHandle,
                              SQLSMALLINT *ParameterCountPtr);
extern SQLRETURN SQLRowCount(SQLHSTMT StatementHandle, SQLLEN *RowCountPtr);
extern SQLRETURN SQLDescribeColW(SQLHSTMT StatementHandle,
                                 SQLUSMALLINT ColumnNumber,
                                 SQLWCHAR *ColumnName,
                                 SQLSMALLINT BufferLength,
                                 SQLSMALLINT *NameLengthPtr,
                                 SQLSMALLINT *DataTypePtr,
                                 SQLULEN *ColumnSizePtr,
                                 SQLSMALLINT *DecimalDigitsPtr,
                                 SQLSMALLINT *NullablePtr);
extern SQLRETURN SQLGetData(SQLHSTMT StatementHandle, SQLUSMALLINT Col_or_Param_Num,
                            SQLSMALLINT TargetType, SQLPOINTER TargetValuePtr,
                            SQLLEN BufferLength, SQLLEN *StrLen_or_IndPtr);
extern SQLRETURN SQLBindParameter(SQLHSTMT StatementHandle,
                                  SQLUSMALLINT ParameterNumber,
                                  SQLSMALLINT InputOutputType,
                                  SQLSMALLINT ValueType,
                                  SQLSMALLINT ParameterType,
                                  SQLULEN ColumnSize,
                                  SQLSMALLINT DecimalDigits,
                                  SQLPOINTER ParameterValuePtr,
                                  SQLLEN BufferLength,
                                  SQLLEN *StrLen_or_IndPtr);
extern SQLRETURN SQLFreeStmt(SQLHSTMT StatementHandle, SQLUSMALLINT Option);
extern SQLRETURN SQLGetDiagRecW(SQLSMALLINT HandleType, SQLHANDLE Handle,
                                SQLSMALLINT RecNumber, SQLWCHAR *SQLState,
                                SQLINTEGER *NativeErrorPtr,
                                SQLWCHAR *MessageText, SQLSMALLINT BufferLength,
                                SQLSMALLINT *TextLengthPtr);

/* -------------------------------------------------------------- binding shims
 * These are the spec functions. Each one keeps the real ODBC handle in C and
 * uses the v2 kinds; text crossing the boundary is UTF-8. */

/* Connection. A NULL return is a failed open; the diagnostic, SQLSTATE and
 * native code are read with the three accessors below. */
extern ms_conn *ms_open(const void *dsn, int n);
extern const char *ms_open_error(void);
extern const char *ms_open_sqlstate(void);
extern int ms_open_error_code(void);
extern int ms_close_conn(ms_conn *c);
extern const char *ms_conn_diag(const ms_conn *c);
extern const char *ms_conn_sqlstate(const ms_conn *c);
extern int ms_conn_error_code(const ms_conn *c);

/* Statements. exec_direct/prepare write the new statement even on error so the
 * caller can read the diagnostic and close it exactly once. */
extern int ms_exec_direct(ms_conn *c, const void *sql, int n, ms_stmt **out);
extern int ms_prepare(ms_conn *c, const void *sql, int n, ms_stmt **out);
extern int ms_execute(ms_stmt *s);
extern int ms_fetch(ms_stmt *s);
extern int ms_num_cols(ms_stmt *s);
extern int ms_num_params(ms_stmt *s);
extern long ms_row_count(ms_stmt *s);
extern const char *ms_col_name(ms_stmt *s, int col);
extern int ms_col_type(ms_stmt *s, int col);
extern long ms_col_size(ms_stmt *s, int col);
extern int ms_col_digits(ms_stmt *s, int col);
extern int ms_col_nullable(ms_stmt *s, int col);
extern int ms_close_stmt(ms_stmt *s);
extern const char *ms_stmt_diag(ms_stmt *s);
extern const char *ms_stmt_sqlstate(ms_stmt *s);
extern int ms_stmt_error_code(ms_stmt *s);
extern int ms_stmt_live(ms_stmt *s);

/* Cell reads. read_cell returns 0 for a value, 1 for SQL NULL, -1 for a driver
 * error and -2 for a value over MS_MAX_VALUE. The cell_value/cell_len pair is
 * the cbytes value and length function pair: read_cell must run first. */
extern int ms_read_cell(ms_stmt *s, int col, int binary);
extern const void *ms_cell_value(ms_stmt *s, int col, int binary);
extern int ms_cell_len(ms_stmt *s, int col, int binary);

/* Typed parameters. Every value is copied into a per-statement slot, so the
 * caller's buffer need not outlive the bind; the slots are freed on close or
 * ms_bind_clear. */
extern int ms_bind_null(ms_stmt *s, int index);
extern int ms_bind_int(ms_stmt *s, int index, long value);
extern int ms_bind_text(ms_stmt *s, int index, const void *utf8, int n);
extern int ms_bind_binary(ms_stmt *s, int index, const void *data, int n);
extern int ms_bind_clear(ms_stmt *s);

/* Diagnostic formatting: all records as UTF-8 "SQLSTATE/native/message" lines.
 * handle_type is SQL_HANDLE_DBC or SQL_HANDLE_STMT. Returns the byte length
 * written (excluding the NUL) and always NUL-terminates when cap > 0. */
extern int ms_diag(SQLHANDLE h, int handle_type, char *out, int cap);

#endif
