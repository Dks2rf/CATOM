import hashlib
import os
import shutil
import time
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from datetime import datetime
from mutagen import File as MutagenFile

DATABASE_URL = "sqlite:///./music.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)


class Track(Base):
    __tablename__ = "tracks"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    album = Column(String, nullable=True)
    duration = Column(Float, default=0)
    file_path = Column(String, unique=True, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class PlayHistory(Base):
    __tablename__ = "play_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=False)
    played_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Music App API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MUSIC_DIR = "music"
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return RedirectResponse("/static/index.html")


def hash_password(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


def extract_metadata(path: str, filename: str):
    title = os.path.splitext(filename)[0]
    artist = "Unknown"
    album = None
    duration = 0.0
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            if audio.get("title"):
                title = audio["title"][0]
            if audio.get("artist"):
                artist = audio["artist"][0]
            if audio.get("album"):
                album = audio["album"][0]
            if audio.info:
                duration = float(audio.info.length)
    except Exception:
        pass
    return title, artist, album, duration


@app.post("/users")
def create_user(username: str, password: str, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "Пользователь существует")
    u = User(username=username, password_hash=hash_password(password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": u.id, "username": u.username}


@app.post("/tracks/upload")
def upload_track(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".mp3", ".flac", ".ogg", ".wav", ".m4a")):
        raise HTTPException(400, "Неподдерживаемый формат")
    safe_name = f"{int(time.time())}_{file.filename}"
    dst = os.path.join(MUSIC_DIR, safe_name)
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    title, artist, album, duration = extract_metadata(dst, file.filename)
    track = Track(title=title, artist=artist, album=album, duration=duration, file_path=dst)
    db.add(track)
    db.commit()
    db.refresh(track)
    return {
        "id": track.id, "title": track.title, "artist": track.artist,
        "album": track.album, "duration": track.duration,
    }


@app.get("/tracks")
def list_tracks(q: Optional[str] = Query(None), db: Session = Depends(get_db)):
    query = db.query(Track)
    if q:
        like = f"%{q}%"
        query = query.filter((Track.title.ilike(like)) | (Track.artist.ilike(like)))
    tracks = query.order_by(Track.uploaded_at.desc()).all()
    return [
        {"id": t.id, "title": t.title, "artist": t.artist,
         "album": t.album, "duration": t.duration}
        for t in tracks
    ]


@app.get("/tracks/{track_id}/stream")
def stream_track(track_id: int, db: Session = Depends(get_db)):
    track = db.query(Track).get(track_id)
    if not track or not os.path.exists(track.file_path):
        raise HTTPException(404, "Трек не найден")
    return FileResponse(track.file_path, media_type="audio/mpeg")


@app.post("/users/{user_id}/play/{track_id}")
def log_play(user_id: int, track_id: int, db: Session = Depends(get_db)):
    db.add(PlayHistory(user_id=user_id, track_id=track_id))
    db.commit()
    return {"status": "ok"}


@app.get("/check")
def check():
    files = os.listdir("static") if os.path.isdir("static") else []
    return {"cwd": os.getcwd(), "files_in_static": files}
