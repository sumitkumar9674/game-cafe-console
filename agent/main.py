import socket


def get_pc_name():
    """Return the Windows computer name."""
    return socket.gethostname()


def main():
    pc_name = get_pc_name()

    print("==============================")
    print("      GAME CAFE AGENT")
    print("==============================")
    print(f"PC Name: {pc_name}")
    print("Status: Agent is running")
    print("==============================")


if __name__ == "__main__":
    main()