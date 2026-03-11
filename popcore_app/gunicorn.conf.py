workers = 2
bind = "0.0.0.0:5000"
accesslog = "-"
errorlog = "-"
timeout = 120  # long timeout for scraper requests
preload_app = True  # load app in master process before forking workers (prevents migration race)
