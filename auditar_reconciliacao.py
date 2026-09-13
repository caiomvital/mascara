"""
Auditoria de inconsistências na reconciliação Situação x Turma de Controle.

Roda a MESMA analise da reconciliacao (leitura, nada gravado), mas depois
relê os comentarios de cada aluno com padroes mais amplos que os usados na
analise real, procurando falsos negativos - coisa que o comentario diz e a
deteccao apertada (usada pra nao agir errado) nao pegou. Achado real que
motivou isto: caso NORMANDO NOBLAT, onde o titulo de um comentario de
boleto fugia do padrao esperado e a fonte de vencimento usada foi uma
matricula antiga e errada, inflando a elegibilidade dele incorretamente.

So LEITURA - nunca corrige nada sozinho, so gera um relatorio pra revisao
humana.

Como rodar:
  set FUCTURA_LOGIN=seu_login
  set FUCTURA_SENHA=sua_senha
  python auditar_reconciliacao.py --limite 50
  python auditar_reconciliacao.py --relatorio achados.csv
"""
import argparse
import csv
import os
import sys

import reconciliacao_devedor as rec
from fuctura_client import FucturaClient, FucturaAuthError


def login_fuctura():
    login = os.environ.get("FUCTURA_LOGIN")
    senha = os.environ.get("FUCTURA_SENHA")
    if not login or not senha:
        print("Defina FUCTURA_LOGIN e FUCTURA_SENHA como variável de ambiente antes de rodar.")
        sys.exit(1)
    print(f"Logando como {login}...")
    client = FucturaClient(login, senha)
    try:
        client.entrar()
    except FucturaAuthError as e:
        print(f"Falha no login: {e}")
        sys.exit(1)
    print(f"Login OK - {client.nome_usuario}")
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limite", type=int, default=None, help="número máximo de devedores a auditar (default: todos)")
    parser.add_argument("--dias-minimos", type=int, default=rec.PRAZO_PADRAO_DIAS, help=f"prazo mínimo pra elegibilidade, em dias (default: {rec.PRAZO_PADRAO_DIAS})")
    parser.add_argument("--relatorio", metavar="ARQUIVO.csv", help="exporta os achados pra este CSV")
    args = parser.parse_args()

    client = login_fuctura()
    devedores = rec.listar_devedores_para_analise(client)
    if args.limite:
        devedores = devedores[:args.limite]
    print(f"Auditando {len(devedores)} devedor(es)...\n")

    linhas_relatorio = []
    total_com_achado = 0
    for i, aluno_bruto in enumerate(devedores, 1):
        id_aluno = aluno_bruto["id_aluno"]
        try:
            perfil = client.perfil_aluno(id_aluno)
            comentarios = client.comentarios_aluno(id_aluno)
        except Exception as e:
            print(f"  [{i}/{len(devedores)}] ERRO buscando dados de {aluno_bruto['nome']}: {e}")
            continue

        analise = rec.analisar_aluno(
            client, aluno_bruto, dias_minimos=args.dias_minimos, perfil=perfil, comentarios=comentarios
        )
        achados = rec.detectar_inconsistencias(analise, comentarios)

        if achados:
            total_com_achado += 1
            print(f"\n=== {analise['nome']} (id {id_aluno}) - caso: {analise['caso']} ===")
            for a in achados:
                print(f"  ⚠ {a}")
                linhas_relatorio.append({"id_aluno": id_aluno, "nome": analise["nome"], "caso": analise["caso"], "achado": a})
        elif i % 25 == 0:
            print(f"  [{i}/{len(devedores)}] auditados, {total_com_achado} com achado até aqui...")

    print(f"\n{'=' * 70}")
    print(f"Total auditado: {len(devedores)} | Com pelo menos 1 achado: {total_com_achado}")

    if args.relatorio and linhas_relatorio:
        with open(args.relatorio, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["id_aluno", "nome", "caso", "achado"])
            w.writeheader()
            w.writerows(linhas_relatorio)
        print(f"Relatório exportado para {args.relatorio}")


if __name__ == "__main__":
    main()
