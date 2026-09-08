# Работа над Slivin Harness

Перед проектированием прочитайте [Autonomous Engineering Contract](docs/AUTONOMOUS_ENGINEERING_CONTRACT.md)
и применимые D-записи из [журнала решений](docs/DECISIONS.md), затем актуальную
[архитектуру](docs/ARCHITECTURE.md). Карта решений помогает выбрать нужные записи;
читать все исторические logs для небольшой правки не требуется.

Не возвращайте REJECTED/SUPERSEDED подход без новых проверенных evidence или явного
изменения intent владельцем. Material decision change, причины, рассмотренные
альтернативы, preservation boundaries и предел проверки документируйте в том же
patch. Сохраняйте D-IDs и историю опровергнутых выводов.

Разделяйте code review, synthetic tests, Controller probes, native role sandbox
и full trial. Не подменяйте один уровень PASS другим. Не переносите hidden scenarios,
reference fixes или secrets в generic instructions/tests. Перед изменениями проверяйте
`git status --short`, не перезаписывайте чужую работу; используйте явную UTF-8 кодировку.
