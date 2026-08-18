# Notas Fiscais Favela Llog

Portal online para motoristas enviarem notas fiscais em PDF. O envio é público e não exige login; consulta e download são restritos à administração.

## Recursos

- Nome completo, CPF validado e seleção de 1ª/2ª quinzena.
- Somente PDF, limite de 10 MB e validação do conteúdo.
- PDFs em bucket privado do Supabase Storage.
- Metadados no PostgreSQL com RLS e acesso público bloqueado.
- Área administrativa com login, pesquisa, filtro e download.
- Layout responsivo com identidade Favela Llog.

## Publicar no Render

1. Crie um repositório GitHub com estes arquivos.
2. No Render, escolha **New > Blueprint** e selecione o repositório.
3. Preencha as variáveis secretas pedidas pelo `render.yaml`:
   - `SUPABASE_SECRET_KEY`: chave secreta do projeto Supabase (Settings > API Keys).
   - `ADMIN_PASSWORD`: senha administrativa.
4. O Render cria automaticamente `FLASK_SECRET_KEY`.

Nunca envie o arquivo `.env` ou a chave secreta ao GitHub.

## Desenvolvimento local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
flask --app app run
```

Projeto Supabase: `qvmluieljxpgdqeboiej` (`sa-east-1`).
