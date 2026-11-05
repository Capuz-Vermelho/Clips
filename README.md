# Gerador de clips com IA (grátis)

Cole um link, clique em um botão e baixe os melhores momentos em clips.
Roda no GitHub Actions (grátis em repositório público).

## Configuração (uma vez)
1. Crie um repositório **público** no GitHub e envie estes arquivos.
   O arquivo `clips.yml` deve ficar em `.github/workflows/clips.yml`.
2. Crie uma chave grátis em https://aistudio.google.com
3. No repositório: **Settings > Secrets and variables > Actions > New repository secret**
   - Nome: `GEMINI_API_KEY`  | Valor: sua chave

## Usar
1. Aba **Actions** > **Gerar clips** > **Run workflow**
2. Cole o link, ajuste as opções e rode.
3. Quando terminar, abra a execução e baixe o arquivo **clips** em *Artifacts*.

## Se o YouTube bloquear o download
Servidores do GitHub às vezes são bloqueados. Solução:
exporte seus cookies do YouTube em formato Netscape (extensão "Get cookies.txt LOCALLY"),
e salve o conteúdo como secret `YT_COOKIES`. Use uma conta secundária, por segurança.
Links diretos de arquivos de vídeo (.mp4) funcionam sem isso.
