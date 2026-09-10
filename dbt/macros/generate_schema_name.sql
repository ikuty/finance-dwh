{#
  カスタムスキーマ名を「そのまま」使う（dbt 既定の <target.schema>_<custom> 連結をしない）。
  finance-dwh は cleansed / mart という固定スキーマに出力したいため。
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
