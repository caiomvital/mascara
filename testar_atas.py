"""
Roda o MESMO pipeline de leitura de ata que o app.py usa (processar_ata_fechamento),
direto contra os casos de teste em testes_atas/, sem precisar abrir o navegador
nem subir o servidor web. So LEITURA - nunca grava nada no Fuctura.

Como adicionar um novo caso de teste:
  1. Crie uma pasta em testes_atas/<nome_qualquer>/
  2. Coloque os arquivos da ata (imagens ou PDF) dentro dela
  3. Crie testes_atas/<nome_qualquer>/caso.json com:
     {"turma_id": "1359", "turma_nome": ".J1 26/05/26 TER N", "modo": "fechamento"}

Como rodar:
  set FUCTURA_LOGIN=seu_login
  set FUCTURA_SENHA=sua_senha
  python testar_atas.py [nome_do_caso]

Sem argumento roda todos os casos encontrados em testes_atas/. As credenciais
NAO ficam salvas em nenhum arquivo - so lidas de variavel de ambiente na hora,
pra nao virar segredo comitado sem querer.
"""
import json
import os
import sys
import tempfile

import fechamento_logic as logic
from app import ErroProcessamento, processar_ata_fechamento
from fuctura_client import FucturaClient, FucturaAuthError

TESTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testes_atas")


def listar_casos():
    if not os.path.isdir(TESTES_DIR):
        return []
    casos = []
    for nome in sorted(os.listdir(TESTES_DIR)):
        pasta = os.path.join(TESTES_DIR, nome)
        caso_json = os.path.join(pasta, "caso.json")
        if os.path.isdir(pasta) and os.path.isfile(caso_json):
            with open(caso_json, encoding="utf-8") as f:
                config = json.load(f)
            extensoes_ata = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp")
            arquivos = [
                os.path.join(pasta, f) for f in sorted(os.listdir(pasta))
                if os.path.isfile(os.path.join(pasta, f)) and f.lower().endswith(extensoes_ata)
            ]
            casos.append({"nome": nome, "pasta": pasta, "arquivos": arquivos, **config})
    return casos


def rodar_caso(client, caso):
    print(f"\n{'=' * 70}")
    print(f"CASO: {caso['nome']}  (turma_id={caso['turma_id']}, {len(caso['arquivos'])} arquivo(s))")
    print("=" * 70)

    with tempfile.TemporaryDirectory(prefix="fechamento_teste_") as pasta_paginas:
        try:
            resultado = processar_ata_fechamento(
                client, caso["turma_id"], caso.get("turma_nome", ""), caso.get("modo", "fechamento"),
                caso["arquivos"], pasta_paginas,
            )
        except ErroProcessamento as e:
            print(f"ERRO: {e}")
            return {"caso": caso["nome"], "erro": str(e)}

    rel = resultado["relatorio_turma"]
    print(f"Turma identificada: {resultado['extraido'].get('turma_identificada')}")
    print(f"Confiança geral: {resultado['extraido'].get('confianca_geral')}")
    print(f"Alunos casados: {rel['alunos']}  |  Quantidade de aulas: {rel['quantidade_aulas']}")

    if resultado["extraido"].get("duvidas"):
        print("\nDúvidas:")
        for d in resultado["extraido"]["duvidas"]:
            print(f"  - {d}")

    print("\nTabela de frequência:")
    for linha in rel["tabela"]:
        marca = f"  [{linha['sugestao']}]" if linha["sugestao"] else ""
        print(f"  {linha['nome']:45} {linha['resumo']}{marca}")

    if resultado["nao_casados"]:
        print("\nNão bateram com o roster:")
        for nc in resultado["nao_casados"]:
            print(f"  - {nc['nome']}")
            if nc["contexto"]:
                print(f"      {nc['contexto']}")

    caminho_saida = os.path.join(caso["pasta"], "ultimo_resultado.json")
    with open(caminho_saida, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"\nResultado completo salvo em: {caminho_saida}")

    return {"caso": caso["nome"], "resultado": resultado}


def main():
    login = os.environ.get("FUCTURA_LOGIN")
    senha = os.environ.get("FUCTURA_SENHA")
    if not login or not senha:
        print("Defina FUCTURA_LOGIN e FUCTURA_SENHA como variável de ambiente antes de rodar.")
        print("Exemplo (PowerShell): $env:FUCTURA_LOGIN='seu_login'; $env:FUCTURA_SENHA='sua_senha'; python testar_atas.py")
        sys.exit(1)

    casos = listar_casos()
    if not casos:
        print(f"Nenhum caso de teste encontrado em {TESTES_DIR}")
        sys.exit(1)

    filtro = sys.argv[1] if len(sys.argv) > 1 else None
    if filtro:
        casos = [c for c in casos if c["nome"] == filtro]
        if not casos:
            print(f"Caso '{filtro}' não encontrado.")
            sys.exit(1)

    print(f"Logando como {login}...")
    client = FucturaClient(login, senha)
    try:
        client.entrar()
    except FucturaAuthError as e:
        print(f"Falha no login: {e}")
        sys.exit(1)
    print(f"Login OK - {client.nome_usuario}")

    resultados = [rodar_caso(client, caso) for caso in casos]

    print(f"\n{'=' * 70}")
    print(f"RESUMO: {len(resultados)} caso(s) rodado(s)")
    for r in resultados:
        status = "ERRO" if "erro" in r else "OK"
        print(f"  [{status}] {r['caso']}")


if __name__ == "__main__":
    main()
