"""
Define ou troca a senha de um operador de manutenção (ver manutencao.py).

Rode:  python definir_senha_manutencao.py

Pergunta um usuário e uma senha (sem eco na tela) e imprime UMA linha pra
colar dentro de OPERADORES_MANUTENCAO, no manutencao.py. A senha nunca é
gravada em lugar nenhum - só o hash com salt aleatório.
"""
import getpass

import manutencao


def main():
    print("Configuração de credencial de operador de manutenção.\n")
    usuario = input("Usuário (ex: reserva): ").strip()
    if not usuario:
        print("Usuário vazio, abortando.")
        return
    senha = getpass.getpass("Senha: ")
    senha2 = getpass.getpass("Repita a senha: ")
    if senha != senha2:
        print("As senhas não conferem, abortando.")
        return
    if len(senha) < 10:
        print("Use pelo menos 10 caracteres, abortando.")
        return

    salt_hex, hash_hex = manutencao.gerar_hash_senha(senha)
    print("\nCole esta linha dentro de OPERADORES_MANUTENCAO, em manutencao.py")
    print("(e faça o redeploy):\n")
    print(f'    ("{usuario}", "{hash_hex}", "{salt_hex}", _ITERACOES),\n')


if __name__ == "__main__":
    main()
