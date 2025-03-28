from flask import Flask, render_template, request
from neo4j import GraphDatabase
import os
import pandas as pd


# Update the template folder path
template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)


# Get Neo4j credentials from environment variables with fallback to current values
uri = os.environ.get("NEO4J_URI", "bolt://3.87.3.56:7687")
user = os.environ.get("NEO4J_USER", "neo4j")
password = os.environ.get("NEO4J_PASSWORD", "verse-manifest-inspection")
driver = GraphDatabase.driver(uri, auth=(user, password))


class Neo4jConnection:
    def __init__(self, uri, user, pwd):
        self.__uri = uri
        self.__user = user
        self.__pwd = pwd
        self.__driver = None
        try:
            self.__driver = GraphDatabase.driver(self.__uri, auth=(self.__user, self.__pwd))
        except Exception as e:
            print("Failed to create the driver:", e)
           
    def close(self):
        if self.__driver is not None:
            self.__driver.close()
           
    def query(self, query, parameters=None, db=None):
        assert self.__driver is not None, "Driver not initialized!"
        session = None
        response = None
        try:
            session = self.__driver.session(database=db) if db is not None else self.__driver.session()
            response = list(session.run(query, parameters))
        except Exception as e:
            print("Query failed:", e)
        finally:
            if session is not None:
                session.close()
        return response


# Initialize connection but don't process any data yet
conn = Neo4jConnection(uri="bolt://3.87.3.56:7687",
                      user="neo4j",
                      pwd="verse-manifest-inspection")
conn.close()


def create_project_nodes(tx, name, start_date, deadline, description):
    query = """
        MERGE (p:Project {name: $name})
        SET p.start_date = $start_date,
            p.deadline = $deadline,
            p.description = $description
        RETURN p
    """
    return tx.run(query, name=name, start_date=start_date, deadline=deadline, description=description)


def create_resource_nodes(tx, name):
    query = """
        MERGE (r:Resource {name: $name})
        RETURN r
    """
    return tx.run(query, name=name)


def create_dependency(tx, project_name, dependency_name):
    query = """
        MATCH (p:Project {name: $project})
        MATCH (d:Project {name: $dependency})
        MERGE (p)-[:DEPENDS_ON]->(d)
    """
    return tx.run(query, project=project_name, dependency=dependency_name)


def create_uses_relationship(tx, project_name, resource_name):
    query = """
        MATCH (p:Project {name: $project})
        MATCH (r:Resource {name: $resource})
        MERGE (p)-[:USES]->(r)
    """
    return tx.run(query, project=project_name, resource=resource_name)


def setup_sample_data():
    with driver.session() as session:
        # Create projects with more realistic data
        projects = [
            ("Frontend Development", "2024-01-01", "2024-03-31", "Building the user interface components"),
            ("Backend API", "2024-02-01", "2024-04-30", "Developing REST API endpoints"),
            ("Database Migration", "2024-01-15", "2024-03-15", "Upgrading database schema"),
            ("User Authentication", "2024-02-15", "2024-04-15", "Implementing secure login system"),
            ("Testing Phase", "2024-04-01", "2024-05-31", "Comprehensive testing of all components"),
            ("Deployment", "2024-05-01", "2024-06-15", "Production deployment and monitoring")
        ]
       
        for project in projects:
            session.execute_write(create_project_nodes, *project)
       
        # Create dependencies
        dependencies = [
            ("Frontend Development", "Backend API"),
            ("Backend API", "Database Migration"),
            ("User Authentication", "Database Migration"),
            ("Testing Phase", "Frontend Development"),
            ("Testing Phase", "Backend API"),
            ("Testing Phase", "User Authentication"),
            ("Deployment", "Testing Phase")
        ]
       
        for dep in dependencies:
            session.execute_write(create_dependency, *dep)


@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'file' not in request.files:
            return 'No file uploaded'
       
        file = request.files['file']
        if file.filename == '':
            return 'No file selected'


        try:
            # Read the uploaded CSV
            df = pd.read_csv(file)
           
            # Process the uploaded data
            conn = Neo4jConnection(uri=uri, user=user, pwd=password)
           
            # Clear existing data
            conn.query("MATCH (n) DETACH DELETE n")
           
            # Create project nodes with attributes
            for _, row in df.iterrows():
                conn.query("""
                    MERGE (p:Project {name: $name})
                    SET p.start_date = $start_date, 
                        p.deadline = $deadline,
                        p.description = $description
                """, {
                    "name": row["Project Name"],
                    "start_date": row["Start Date"],
                    "deadline": row["Deadline"],
                    "description": row["Description"]
                })
               
            # Create dependency relationships
            for _, row in df.iterrows():
                project = row["Project Name"]
                dependencies = str(row["Dependencies"]).split(", ") if pd.notna(row["Dependencies"]) else []
                for dep in dependencies:
                    if dep.strip():  # Only process non-empty dependencies
                        conn.query("""
                            MATCH (p:Project {name: $project}), (d:Project {name: $dependency})
                            MERGE (p)-[:DEPENDS_ON]->(d)
                        """, {"project": project, "dependency": dep.strip()})

            # Create resource nodes and relationships
            for _, row in df.iterrows():
                project = row["Project Name"]
                resources = str(row["Resources"]).split(", ") if pd.notna(row["Resources"]) else []
                for resource in resources:
                    if resource.strip():  # Only process non-empty resources
                        # First create the resource node if it doesn't exist
                        conn.query("""
                            MERGE (r:Resource {name: $name})
                        """, {"name": resource.strip()})
                        
                        # Then create the relationship
                        conn.query("""
                            MATCH (p:Project {name: $project}), (r:Resource {name: $resource})
                            MERGE (p)-[:USES]->(r)
                        """, {"project": project, "resource": resource.strip()})
           
            conn.close()
        except Exception as e:
            return f'Error processing file: {str(e)}'


    # Fetch and display projects
    def fetch_projects(tx):
        result = tx.run("""
            MATCH (p:Project)
            OPTIONAL MATCH (p)-[:DEPENDS_ON]->(d:Project)
            OPTIONAL MATCH (p)-[:USES]->(r:Resource)
            RETURN p.name AS name,
                   p.start_date AS start,
                   p.deadline AS deadline,
                   p.description AS description,
                   collect(DISTINCT d.name) AS dependencies,
                   collect(DISTINCT r.name) AS resources
        """)
        return [record.data() for record in result]

    # Fetch all resources
    def fetch_resources(tx):
        result = tx.run("""
            MATCH (r:Resource)
            RETURN r.name AS name
        """)
        return [record["name"] for record in result]


    with driver.session() as session:
        projects = session.execute_read(fetch_projects)
        resources = session.execute_read(fetch_resources)


    return render_template("index.html", projects=projects, resources=resources)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
