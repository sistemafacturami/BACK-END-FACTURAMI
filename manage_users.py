"""
FACTURAMI — Utilidad para gestionar contraseñas de usuarios.
Uso:
  python manage_users.py create <usuario> <contraseña>
  python manage_users.py list
  python manage_users.py reset <usuario> <nueva_contraseña>
"""
import json, os, sys
import bcrypt

USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')

def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_user(username: str, password: str, role: str = 'admin'):
    users = load_users()
    if any(u['username'] == username for u in users):
        print(f"[ERROR] El usuario '{username}' ya existe.")
        return False
    users.append({
        'username': username,
        'password_hash': hash_password(password),
        'role': role
    })
    save_users(users)
    print(f"[OK] Usuario '{username}' creado con rol '{role}'.")
    return True

def list_users():
    users = load_users()
    if not users:
        print("[INFO] No hay usuarios registrados.")
        return
    print(f"\n{'Usuario':<20} {'Rol':<15}")
    print("-" * 35)
    for u in users:
        print(f"{u['username']:<20} {u.get('role','admin'):<15}")

def reset_password(username: str, new_password: str):
    users = load_users()
    for u in users:
        if u['username'] == username:
            u['password_hash'] = hash_password(new_password)
            save_users(users)
            print(f"[OK] Contraseña de '{username}' actualizada.")
            return True
    print(f"[ERROR] Usuario '{username}' no encontrado.")
    return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'create' and len(sys.argv) >= 4:
        create_user(sys.argv[2], sys.argv[3])
    elif cmd == 'list':
        list_users()
    elif cmd == 'reset' and len(sys.argv) >= 4:
        reset_password(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
