FROM python:3.5

ADD . /opt/mailman/

WORKDIR /opt/mailman/

RUN python setup.py develop

RUN mailman -C mailman-testing.cfg start

EXPOSE 9001

CMD ["tail", "-f", "/dev/null"]
