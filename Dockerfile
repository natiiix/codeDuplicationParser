FROM python:3.7

WORKDIR /usr/src/app

COPY . .

RUN apt-get -y update && apt-get -y dist-upgrade && apt-get install -y postgresql sudo && service postgresql start && \
    echo "CREATE USER root;\nCREATE DATABASE cyclone OWNER root;" | sudo -u postgres psql && \
    psql -f ./web/prepare_tables.pgsql cyclone && \
    pip install --upgrade --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD [ "./run_web.sh" ]
