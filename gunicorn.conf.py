def post_fork(server, worker):
    from app import init_pool
    init_pool()