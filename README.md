 Excel Metadata Search

A Python CLI for searching Excel metadata catalogs. Returns one result per field with source details and match explanations. All processing is local; source files are never modified.

## Usage

Requires Python 3.12+. Run from the project directory:

```powershell
python -m pip install -r requirements.txt
python app.py --check
python app.py --query "总部企业"
python app.py --query "company_name" --page 1 --page-size 10
python app.py --query "企业 name" --data-dir "D:\Catalogs"
```

On the current machine, use `.\.runtime\python.exe -X utf8` instead of `python`; dependencies are already installed. This local runtime is not included in the repository.

`--data-dir` accepts a folder, not a file, and defaults to the project directory. Each run reloads its Excel files without searching subfolders. Results are JSON; `--help` lists all options.

## Excel Format

Each `.xlsx` or `.xlsm` file represents one system, named after the file. Only the first two sheets are read:

| Sheet | Content | Required columns |
|---|---|---|
| First | Table metadata | `数据表名`, `业务数据资源名称` or `表注释` |
| Second | Field metadata | `数据表名`, `数据字段名` |

Optional columns include `数据库名`, `数据字段注释` and `数据样例`. Supported header aliases are defined in `metadata_search\loader.py`.

Rows are joined by database and table name within each workbook. If the first sheet has no database column, only table name is used. Template rows are skipped; original field records, including duplicates and unmatched rows, are retained.

Results include system name, table comment, field comment, sample data, database name, table name and field name.

## Search Rules

- **Chinese:** search system names, table comments, field comments and samples. Rank by full-query match count, full-query weight, additional subword match count, then subword weight. Each dimension counts once; full-query matches always come first.
- **English:** search table and field identifiers, ignoring case and splitting snake_case and camelCase. Exact identifiers rank above whole-token matches; tokens spanning both identifiers rank last.
- **Mixed queries:** both languages must match the same field record, with Chinese ranking taking precedence.

Ties preserve filename and source-row order. No TF-IDF, translation or synonym expansion is used.

## Configuration

Edit `config.json` to adjust weights and Chinese subword rules:

| Setting | Purpose |
|---|---|
| `weights` | Sample 40 > system 30 > table comment 20 > field comment 10; preserve this order |
| `auto_subwords` | Extract subwords using jieba |
| `subword_overrides` | Replace automatic extraction, e.g. `"总部企业": ["总部"]`; `[]` disables expansion for that query |
| `excluded_subwords` | Exclude specified subwords globally |
| `custom_words` | Add domain vocabulary to the tokenizer |

Subwords must contain at least two Chinese characters and be proper substrings of the query.
