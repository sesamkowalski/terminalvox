# Run the voice server in a terminal (make run) or in the background as a systemd user service.
SERVICE := terminalvox
UNIT    := $(HOME)/.config/systemd/user/$(SERVICE).service
LOG_DIR ?= $(CURDIR)/logs
SYSTEMCTL := systemctl --user

.PHONY: help run install uninstall start stop restart status logs journal

help:
	@echo "make run        - run in the terminal (Ctrl+C to stop)"
	@echo "make install    - install the service and start it at login"
	@echo "make start | stop | restart | status"
	@echo "make logs       - tail $(LOG_DIR)/bot.log"
	@echo "make journal    - systemd journal (run.sh output, startup errors)"
	@echo "make uninstall  - stop and remove the service"

run:
	./run.sh

# The unit is regenerated from the template when the template or LOG_DIR changes.
$(UNIT): $(SERVICE).service.in Makefile
	mkdir -p $(@D) $(LOG_DIR)
	sed -e 's#@DIR@#$(CURDIR)#g' -e 's#@LOG_DIR@#$(LOG_DIR)#g' $< > $@
	$(SYSTEMCTL) daemon-reload

install: $(UNIT)
	$(SYSTEMCTL) enable $(SERVICE)

start: $(UNIT)
	$(SYSTEMCTL) start $(SERVICE)

stop:
	$(SYSTEMCTL) stop $(SERVICE)

restart: $(UNIT)
	$(SYSTEMCTL) restart $(SERVICE)

status:
	-$(SYSTEMCTL) --no-pager status $(SERVICE)

logs:
	tail -n 100 -F $(LOG_DIR)/bot.log

journal:
	journalctl --user -u $(SERVICE) -n 100 -f

uninstall:
	-$(SYSTEMCTL) disable --now $(SERVICE)
	rm -f $(UNIT)
	$(SYSTEMCTL) daemon-reload
